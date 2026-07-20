# Backtesting via Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `cli.py backtest ...` command group that replays the existing paper-trading ledger/risk-check/record-fill machinery against historical price data, in two modes: a fast deterministic Python loop (`backtest run`) and per-day building blocks (`backtest state/quote/risk-check/record-fill/check-stop-losses/trading-days`) that the daily-cycle skill's LLM-driven mode calls one simulated day at a time.

**Architecture:** `backtest_data.py` holds a network-free `HistoricalPriceStore` that takes an injectable `HistoricalDataFetcher` (mirroring the `universe.py`/`universe_client.py` split) and caches per-symbol OHLC to disk; it guarantees no-lookahead by always filtering to dates on or before the requested `as-of` date, no matter how much of the future is already cached. `universe_client.py` gains `LiveHistoricalDataFetcher`, the one class in this plan that calls `yfinance`. `backtest_commands.py` resolves a `--run` id to an isolated `data/backtests/<run_id>/` ledger/trade-log/equity-curve, forwards the per-day commands unchanged to `commands.py`, and holds the two pieces of new orchestration logic: the daily volatility re-ranking and the deterministic loop. `cli.py` gains a `backtest` subcommand group.

**Tech Stack:** Python 3.11+, `yfinance` (already a dependency) for historical OHLC, `pytest` for testing. No new dependencies.

## Global Constraints

- `backtest_data.py` stays network-free and fully unit-testable, exactly like `universe.py`: every function that needs external data takes a `HistoricalDataFetcher` (or already-fetched values) as a parameter. All real network I/O for historical data lives in `universe_client.py`'s `LiveHistoricalDataFetcher` — the only new class in this plan that touches the network.
- **No-lookahead is non-negotiable:** any function that answers "what did the market look like as of date X" must never return data for a date after X, even if the in-memory/on-disk cache already holds later dates (e.g. because an earlier call warmed the cache with a wider range). This is proven directly in Task 3's tests, not just assumed.
- A missing/failed historical price for a symbol is never fabricated or estimated — that symbol is simply skipped for the affected date(s), consistent with the existing "never fabricate a price" rule in `universe_client.py` and `commands.py`.
- If the trading-day calendar can't be derived (the benchmark symbol's own fetch fails), the failure propagates — no silent fallback to a guessed calendar.
- **Two refinements beyond the design spec's literal CLI surface table**, both load-bearing for correctness and noted here so they don't look like scope creep mid-plan:
  - `backtest state` gains an optional `--prices-json` (defaulting to `{}`), mirroring the live `state` command's own optional flag — needed for the LLM-driven mode's "refresh state with real prices" step, which the spec's narrative implies but the CLI table omitted.
  - Each run gains a third artifact, `data/backtests/<run_id>/equity_curve.csv` (`date,cash,positions_value,total_equity`), written once per simulated day by `backtest run`. Reconstructing accurate mark-to-market equity and max drawdown from `trade_log.csv` alone is impossible (it only records realized cash flows, not the daily value of open positions) — `backtest report` reads this file directly instead.
- New directories `data/backtests/` and `data/historical_price_cache/` are **not** covered by the existing `.gitignore` patterns `data/*.json` / `data/*.csv` — those only match files directly inside `data/`, not nested subdirectories. Task 12 adds `data/backtests/` and `data/historical_price_cache/` to `.gitignore` before the first real (non-`tmp_path`) run.
- Every task ends green (`pytest` passing) before moving to the next, except Task 5, whose `LiveHistoricalDataFetcher` makes real network calls and is verified manually per the design spec's testing strategy (same treatment as `LiveMarketDataClient` in the universe-fetch plan) — it has no pure helper function to unit test, so it adds zero automated tests.
- `python -m robinhood_bot.cli ...` and `pytest` both run via the shared venv at `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe` (this worktree has no `.venv` of its own — the venv lives outside git, in the main checkout, and is shared across worktrees).

---

### Task 1: Historical bar model, fetcher protocol, and cache serialization

**Files:**
- Create: `robinhood_bot/backtest_data.py`
- Test: `tests/test_backtest_data.py`

**Interfaces:**
- Produces: `HistoricalBar(date: date, open: float, high: float, low: float, close: float)`; `HistoricalDataFetcher` Protocol with `fetch_history(symbol: str, start: date, end: date) -> list[HistoricalBar]`; `SymbolCache(start: date, end: date, bars: list[HistoricalBar])`; `load_symbol_cache(path: Path) -> SymbolCache | None`; `save_symbol_cache(path: Path, cache: SymbolCache) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_data.py
from datetime import date

from robinhood_bot.backtest_data import (
    HistoricalBar,
    SymbolCache,
    load_symbol_cache,
    save_symbol_cache,
)


def test_historical_bar_fields():
    bar = HistoricalBar(date=date(2026, 7, 1), open=99.0, high=101.0, low=98.5, close=100.0)
    assert bar.date == date(2026, 7, 1)
    assert bar.open == 99.0
    assert bar.close == 100.0


def test_load_symbol_cache_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "AAPL.json"
    assert load_symbol_cache(path) is None


def test_save_and_load_symbol_cache_round_trip(tmp_path):
    path = tmp_path / "AAPL.json"
    original = SymbolCache(
        start=date(2026, 1, 1),
        end=date(2026, 7, 1),
        bars=[
            HistoricalBar(date(2026, 1, 2), 99.0, 101.0, 98.5, 100.0),
            HistoricalBar(date(2026, 1, 3), 100.0, 102.0, 99.5, 101.0),
        ],
    )
    save_symbol_cache(path, original)
    loaded = load_symbol_cache(path)

    assert loaded.start == date(2026, 1, 1)
    assert loaded.end == date(2026, 7, 1)
    assert loaded.bars[0].date == date(2026, 1, 2)
    assert loaded.bars[1].close == 101.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.backtest_data'`

- [ ] **Step 3: Write minimal implementation**

```python
# robinhood_bot/backtest_data.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol


@dataclass
class HistoricalBar:
    date: date
    open: float
    high: float
    low: float
    close: float


class HistoricalDataFetcher(Protocol):
    def fetch_history(self, symbol: str, start: date, end: date) -> list[HistoricalBar]: ...


@dataclass
class SymbolCache:
    start: date
    end: date
    bars: list[HistoricalBar]


def _bar_to_dict(bar: HistoricalBar) -> dict:
    return {
        "date": bar.date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
    }


def _bar_from_dict(data: dict) -> HistoricalBar:
    return HistoricalBar(
        date=date.fromisoformat(data["date"]),
        open=data["open"],
        high=data["high"],
        low=data["low"],
        close=data["close"],
    )


def _cache_to_dict(cache: SymbolCache) -> dict:
    return {
        "start": cache.start.isoformat(),
        "end": cache.end.isoformat(),
        "bars": [_bar_to_dict(b) for b in cache.bars],
    }


def _cache_from_dict(data: dict) -> SymbolCache:
    return SymbolCache(
        start=date.fromisoformat(data["start"]),
        end=date.fromisoformat(data["end"]),
        bars=[_bar_from_dict(b) for b in data["bars"]],
    )


def load_symbol_cache(path: Path) -> SymbolCache | None:
    if not path.exists():
        return None
    with path.open("r") as f:
        return _cache_from_dict(json.load(f))


def save_symbol_cache(path: Path, cache: SymbolCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(_cache_to_dict(cache), f, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_data.py tests/test_backtest_data.py
git commit -m "feat: add historical bar model and per-symbol cache serialization"
```

---

### Task 2: `HistoricalPriceStore` — cache-aware `get_close`/`get_ohlc`

**Files:**
- Modify: `robinhood_bot/backtest_data.py`
- Modify: `tests/test_backtest_data.py`

**Interfaces:**
- Consumes: `HistoricalBar`, `HistoricalDataFetcher`, `SymbolCache`, `load_symbol_cache`, `save_symbol_cache` (Task 1).
- Produces: `HistoricalPriceStore(fetcher: HistoricalDataFetcher, cache_dir: Path)` with `get_ohlc(symbol: str, on: date) -> HistoricalBar | None` and `get_close(symbol: str, on: date) -> float | None`. Fetches are merged into an on-disk per-symbol cache (`<cache_dir>/<symbol>.json`) so a later query already covered by the cached `[start, end]` range never re-fetches.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_data.py — append
from robinhood_bot.backtest_data import HistoricalPriceStore


def _bars(symbol_dates_closes):
    return [HistoricalBar(d, c, c + 1, c - 1, c) for d, c in symbol_dates_closes]


class FakeHistoricalDataFetcher:
    def __init__(self, bars_by_symbol=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.calls = []

    def fetch_history(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return [b for b in self.bars_by_symbol.get(symbol, []) if start <= b.date <= end]


def test_get_close_returns_price_for_known_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 102.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    assert store.get_close("AAPL", date(2026, 1, 5)) == 102.0


def test_get_close_returns_none_for_missing_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    assert store.get_close("AAPL", date(2026, 1, 3)) is None


def test_repeated_query_for_same_date_does_not_refetch(tmp_path):
    # get_close/get_ohlc each request only the exact single day passed in (no
    # buffer — that's added by get_ohlc_window in Task 3), so two *different*
    # dates each trigger their own fetch; only re-querying the same date is
    # guaranteed to hit the cache at this stage.
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    store.get_close("AAPL", date(2026, 1, 2))
    store.get_close("AAPL", date(2026, 1, 2))

    assert len(fetcher.calls) == 1


def test_cache_persists_across_store_instances(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 102.0)]),
    })
    store_a = HistoricalPriceStore(fetcher, tmp_path)
    store_a.get_close("AAPL", date(2026, 1, 5))

    store_b = HistoricalPriceStore(fetcher, tmp_path)
    price = store_b.get_close("AAPL", date(2026, 1, 5))

    assert price == 102.0
    assert len(fetcher.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: FAIL with `ImportError: cannot import name 'HistoricalPriceStore'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/backtest_data.py`:

```python
class HistoricalPriceStore:
    def __init__(self, fetcher: HistoricalDataFetcher, cache_dir: Path):
        self._fetcher = fetcher
        self._cache_dir = cache_dir
        self._bars: dict[str, dict[date, HistoricalBar]] = {}
        self._ranges: dict[str, tuple[date, date] | None] = {}

    def _cache_path(self, symbol: str) -> Path:
        return self._cache_dir / f"{symbol}.json"

    def _ensure_range(self, symbol: str, start: date, end: date) -> None:
        if symbol not in self._bars:
            cache = load_symbol_cache(self._cache_path(symbol))
            if cache is not None:
                self._bars[symbol] = {b.date: b for b in cache.bars}
                self._ranges[symbol] = (cache.start, cache.end)
            else:
                self._bars[symbol] = {}
                self._ranges[symbol] = None

        cached_range = self._ranges[symbol]
        if cached_range is not None and cached_range[0] <= start and end <= cached_range[1]:
            return

        fetch_start = min(start, cached_range[0]) if cached_range else start
        fetch_end = max(end, cached_range[1]) if cached_range else end

        for bar in self._fetcher.fetch_history(symbol, fetch_start, fetch_end):
            self._bars[symbol][bar.date] = bar
        self._ranges[symbol] = (fetch_start, fetch_end)

        save_symbol_cache(
            self._cache_path(symbol),
            SymbolCache(start=fetch_start, end=fetch_end, bars=list(self._bars[symbol].values())),
        )

    def get_ohlc(self, symbol: str, on: date) -> HistoricalBar | None:
        self._ensure_range(symbol, on, on)
        return self._bars[symbol].get(on)

    def get_close(self, symbol: str, on: date) -> float | None:
        bar = self.get_ohlc(symbol, on)
        return bar.close if bar else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_data.py tests/test_backtest_data.py
git commit -m "feat: add HistoricalPriceStore with cache-aware close/OHLC lookups"
```

---

### Task 3: No-lookahead window queries — `get_ohlc_window`/`get_closes_window`

**Files:**
- Modify: `robinhood_bot/backtest_data.py`
- Modify: `tests/test_backtest_data.py`

**Interfaces:**
- Consumes: `HistoricalPriceStore` (Task 2).
- Produces: `get_ohlc_window(symbol: str, end_date: date, window_days: int) -> list[HistoricalBar]` (up to `window_days` trailing bars ending at, and never after, `end_date`); `get_closes_window(symbol: str, end_date: date, window_days: int) -> list[float]` (same, closes only).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_data.py — append
def test_get_closes_window_returns_trailing_closes_ending_at_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([
            (date(2026, 1, 2), 100.0),
            (date(2026, 1, 3), 101.0),
            (date(2026, 1, 4), 102.0),
            (date(2026, 1, 5), 103.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    closes = store.get_closes_window("AAPL", date(2026, 1, 4), window_days=2)

    assert closes == [101.0, 102.0]


def test_get_closes_window_never_includes_dates_after_end_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([
            (date(2026, 1, 2), 100.0),
            (date(2026, 1, 3), 101.0),
            (date(2026, 1, 4), 102.0),
            (date(2026, 1, 5), 103.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    closes = store.get_closes_window("AAPL", date(2026, 1, 3), window_days=10)

    assert closes == [100.0, 101.0]


def test_get_closes_window_excludes_future_bars_already_present_in_cache(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([
            (date(2026, 1, 2), 100.0),
            (date(2026, 1, 3), 101.0),
            (date(2026, 1, 4), 102.0),
            (date(2026, 1, 5), 103.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)
    # Warm the cache with the full range first, as `backtest run` would do.
    store.get_ohlc_window("AAPL", date(2026, 1, 5), window_days=10)

    closes = store.get_closes_window("AAPL", date(2026, 1, 3), window_days=10)

    assert closes == [100.0, 101.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: FAIL with `AttributeError: 'HistoricalPriceStore' object has no attribute 'get_closes_window'`

- [ ] **Step 3: Write minimal implementation**

Change the `date` import line at the top of `robinhood_bot/backtest_data.py` from:

```python
from datetime import date
```

to:

```python
from datetime import date, timedelta
```

Then append to the `HistoricalPriceStore` class:

```python
    def get_ohlc_window(self, symbol: str, end_date: date, window_days: int) -> list[HistoricalBar]:
        # Fetch a generous calendar-day buffer so `window_days` *trading* days
        # are available even across weekends/holidays; the trailing-slice
        # below is what actually enforces no-lookahead, not this buffer.
        fetch_start = end_date - timedelta(days=window_days * 2 + 10)
        self._ensure_range(symbol, fetch_start, end_date)
        dates = sorted(d for d in self._bars[symbol] if d <= end_date)
        trailing = dates[-window_days:]
        return [self._bars[symbol][d] for d in trailing]

    def get_closes_window(self, symbol: str, end_date: date, window_days: int) -> list[float]:
        return [bar.close for bar in self.get_ohlc_window(symbol, end_date, window_days)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_data.py tests/test_backtest_data.py
git commit -m "feat: add no-lookahead trailing window queries to HistoricalPriceStore"
```

---

### Task 4: Trading-day calendar derivation

**Files:**
- Modify: `robinhood_bot/backtest_data.py`
- Modify: `tests/test_backtest_data.py`

**Interfaces:**
- Consumes: `HistoricalPriceStore` (Task 2).
- Produces: `trading_days(benchmark_symbol: str, start: date, end: date) -> list[date]` — the benchmark symbol's own historical dates within `[start, end]`, ascending. Propagates any fetch failure rather than guessing at a calendar.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_data.py — append
import pytest


def test_trading_days_excludes_weekends_via_benchmark_dates(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "SPY": _bars([
            (date(2026, 1, 2), 400.0),  # Friday
            (date(2026, 1, 5), 402.0),  # Monday
            (date(2026, 1, 6), 403.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    days = store.trading_days("SPY", date(2026, 1, 1), date(2026, 1, 6))

    assert days == [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]


def test_trading_days_raises_when_benchmark_fetch_fails(tmp_path):
    class FailingFetcher:
        def fetch_history(self, symbol, start, end):
            raise RuntimeError("network error")

    store = HistoricalPriceStore(FailingFetcher(), tmp_path)

    with pytest.raises(RuntimeError):
        store.trading_days("SPY", date(2026, 1, 1), date(2026, 1, 6))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: FAIL with `AttributeError: 'HistoricalPriceStore' object has no attribute 'trading_days'`

- [ ] **Step 3: Write minimal implementation**

Append to the `HistoricalPriceStore` class in `robinhood_bot/backtest_data.py`:

```python
    def trading_days(self, benchmark_symbol: str, start: date, end: date) -> list[date]:
        self._ensure_range(benchmark_symbol, start, end)
        return sorted(d for d in self._bars[benchmark_symbol] if start <= d <= end)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_data.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_data.py tests/test_backtest_data.py
git commit -m "feat: derive trading-day calendar from benchmark symbol history"
```

---

### Task 5: `LiveHistoricalDataFetcher` — the real network client

**Files:**
- Modify: `robinhood_bot/universe_client.py`

**Interfaces:**
- Consumes: `HistoricalBar` from `robinhood_bot.backtest_data` (Task 1).
- Produces: `LiveHistoricalDataFetcher` implementing the `HistoricalDataFetcher` protocol (`fetch_history(symbol, start, end) -> list[HistoricalBar]`) via `yf.Ticker(symbol).history(start=, end=)`.

**Note on testing:** like `LiveMarketDataClient`, this class makes real HTTP calls and has no pure helper function to unit test — per the design spec's testing strategy, it's verified once by hand (Step 3 below), not covered by `pytest`.

- [ ] **Step 1: Add the import and the class**

Change the top of `robinhood_bot/universe_client.py` from:

```python
# robinhood_bot/universe_client.py
from __future__ import annotations

import io
import urllib.request

import pandas as pd
import yfinance as yf

from .universe import Bar
```

to:

```python
# robinhood_bot/universe_client.py
from __future__ import annotations

import io
import urllib.request
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from .backtest_data import HistoricalBar
from .universe import Bar
```

Then append this class to the end of the file:

```python
class LiveHistoricalDataFetcher:
    def fetch_history(self, symbol: str, start: date, end: date) -> list[HistoricalBar]:
        try:
            # yfinance's `end` is exclusive, so add a day to make our own
            # [start, end] contract inclusive of `end`.
            history = yf.Ticker(symbol).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), timeout=15
            )
        except Exception:
            return []
        if history.empty:
            return []
        return [
            HistoricalBar(
                date=row.Index.date(),
                open=float(row.Open),
                high=float(row.High),
                low=float(row.Low),
                close=float(row.Close),
            )
            for row in history.itertuples()
        ]
```

- [ ] **Step 2: Run the existing suite to confirm nothing broke**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: PASS (32 tests, unchanged — this task adds no automated tests of its own)

- [ ] **Step 3: Manually verify the live fetcher once**

Run this by hand (not part of the automated suite):

```bash
D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -c "
from datetime import date, timedelta
from robinhood_bot.universe_client import LiveHistoricalDataFetcher
fetcher = LiveHistoricalDataFetcher()
bars = fetcher.fetch_history('AAPL', date.today() - timedelta(days=30), date.today())
print('AAPL bars fetched:', len(bars))
if bars:
    print('first:', bars[0])
    print('last:', bars[-1])
"
```

Expected: roughly 20 bars (trading days in a 30-calendar-day window), with `first`/`last` showing plausible OHLC values. Note any discrepancy in your report.

- [ ] **Step 4: Commit**

```bash
git add robinhood_bot/universe_client.py
git commit -m "feat: add LiveHistoricalDataFetcher for backtest OHLC data"
```

---

### Task 6: Run-id path resolution and thin command wrappers

**Files:**
- Create: `robinhood_bot/backtest_commands.py`
- Test: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `commands.cmd_state/cmd_risk_check/cmd_record_fill/cmd_check_stop_losses` (existing `commands.py`); `HistoricalPriceStore` (Task 2, 4).
- Produces: `RunPaths(ledger: Path, trade_log: Path, equity_curve: Path)`; `resolve_run_paths(run_id: str, base_dir: Path) -> RunPaths`; `cmd_backtest_state(run_id, base_dir, starting_cash, prices, asof) -> dict`; `cmd_backtest_quote(symbol, asof, store) -> dict`; `cmd_backtest_risk_check(run_id, base_dir, starting_cash, action, symbol, proposed_value, prices, cfg) -> dict`; `cmd_backtest_record_fill(run_id, base_dir, starting_cash, action, symbol, qty, price, asof, reason) -> dict`; `cmd_backtest_check_stop_losses(run_id, base_dir, starting_cash, prices, asof, cfg, apply) -> dict`; `cmd_backtest_trading_days(start, end, store, benchmark_symbol="SPY") -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_commands.py
from datetime import date

import pytest

from robinhood_bot import backtest_commands, ledger
from robinhood_bot.backtest_data import HistoricalBar, HistoricalPriceStore
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState
from robinhood_bot.risk_engine import RiskConfig


def test_resolve_run_paths_groups_files_under_run_id(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)

    assert paths.ledger == tmp_path / "run1" / "ledger.json"
    assert paths.trade_log == tmp_path / "run1" / "trade_log.csv"
    assert paths.equity_curve == tmp_path / "run1" / "equity_curve.csv"


def test_cmd_backtest_state_reads_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=5_000.0))

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5),
    )

    assert result["cash"] == 5_000.0
    assert result["trading_mode"] == "backtest"


def test_cmd_backtest_quote_returns_price_from_store(tmp_path):
    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [HistoricalBar(date(2026, 1, 5), 100.0, 101.0, 99.0, 100.5)]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_quote("AAPL", date(2026, 1, 5), store)

    assert result == {"symbol": "AAPL", "date": "2026-01-05", "price": 100.5}


def test_cmd_backtest_quote_returns_none_price_when_missing(tmp_path):
    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return []

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_quote("AAPL", date(2026, 1, 5), store)

    assert result["price"] is None


def test_cmd_backtest_risk_check_uses_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=10_000.0, month_start_equity=10_000.0))
    cfg = RiskConfig(max_position_pct=0.20)

    result = backtest_commands.cmd_backtest_risk_check(
        "run1", tmp_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={}, cfg=cfg,
    )

    assert result["approved"] is True


def test_cmd_backtest_record_fill_writes_isolated_trade_log(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=10_000.0))

    result = backtest_commands.cmd_backtest_record_fill(
        "run1", tmp_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, asof=date(2026, 1, 5), reason="test",
    )

    assert result["cash"] == 8_500.0
    assert paths.trade_log.exists()
    reloaded = ledger.load_state(paths.ledger, starting_cash=0.0)
    assert reloaded.active_positions[0].symbol == "MSFT"


def test_cmd_backtest_check_stop_losses_uses_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 1, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(profit_target_pct=0.08)

    result = backtest_commands.cmd_backtest_check_stop_losses(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 110.0}, asof=date(2026, 1, 10),
        cfg=cfg, apply=True,
    )

    assert result["results"][0]["action"] == "SELL"


def test_cmd_backtest_trading_days_returns_isoformat_dates(tmp_path):
    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                HistoricalBar(date(2026, 1, 5), 402.0, 403.0, 401.0, 402.0),
            ]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_trading_days(date(2026, 1, 1), date(2026, 1, 6), store)

    assert result["trading_days"] == ["2026-01-02", "2026-01-05"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.backtest_commands'`

- [ ] **Step 3: Write minimal implementation**

```python
# robinhood_bot/backtest_commands.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import commands
from .backtest_data import HistoricalPriceStore
from .risk_engine import RiskConfig


@dataclass
class RunPaths:
    ledger: Path
    trade_log: Path
    equity_curve: Path


def resolve_run_paths(run_id: str, base_dir: Path) -> RunPaths:
    run_dir = base_dir / run_id
    return RunPaths(
        ledger=run_dir / "ledger.json",
        trade_log=run_dir / "trade_log.csv",
        equity_curve=run_dir / "equity_curve.csv",
    )


def cmd_backtest_state(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_state(paths.ledger, starting_cash, prices, asof, trading_mode="backtest")


def cmd_backtest_quote(symbol: str, asof: date, store: HistoricalPriceStore) -> dict:
    return {"symbol": symbol, "date": asof.isoformat(), "price": store.get_close(symbol, asof)}


def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg)


def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason,
    )


def cmd_backtest_check_stop_losses(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
    cfg: RiskConfig, apply: bool,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_check_stop_losses(paths.ledger, starting_cash, prices, asof, cfg, apply)


def cmd_backtest_trading_days(
    start: date, end: date, store: HistoricalPriceStore, benchmark_symbol: str = "SPY",
) -> dict:
    days = store.trading_days(benchmark_symbol, start, end)
    return {"trading_days": [d.isoformat() for d in days]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: add backtest run-id path resolution and thin command wrappers"
```

---

### Task 7: Candidate re-ranking as of a simulated day

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Modify: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `HistoricalPriceStore.get_closes_window`/`get_ohlc_window` (Task 3); `realized_volatility`, `average_true_range_pct`, `percentile_ranks` from `robinhood_bot.universe` (existing).
- Produces: `rank_candidates_as_of(symbols: list[str], store: HistoricalPriceStore, today: date, vol_window_days: int = 20, atr_window_days: int = 14) -> list[str]` — symbols sorted descending by the same "both" combined-rank formula `universe.build_universe` uses, computed only from data on or before `today`. A symbol with fewer than 2 bars of history as of `today` is dropped.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_commands.py — append
def test_rank_candidates_as_of_ranks_by_recent_volatility(tmp_path):
    low_bars = [
        HistoricalBar(date(2026, 1, 1), 100.0, 100.5, 99.5, 100.0),
        HistoricalBar(date(2026, 1, 2), 100.0, 100.5, 99.7, 100.1),
        HistoricalBar(date(2026, 1, 3), 100.1, 100.6, 99.8, 100.05),
    ]
    high_bars = [
        HistoricalBar(date(2026, 1, 1), 100.0, 110.0, 90.0, 100.0),
        HistoricalBar(date(2026, 1, 2), 100.0, 115.0, 85.0, 105.0),
        HistoricalBar(date(2026, 1, 3), 105.0, 120.0, 80.0, 95.0),
    ]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            bars = {"LOW": low_bars, "HIGH": high_bars}[symbol]
            return [b for b in bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    ranked = backtest_commands.rank_candidates_as_of(
        ["LOW", "HIGH"], store, date(2026, 1, 3), vol_window_days=2, atr_window_days=2,
    )

    assert ranked == ["HIGH", "LOW"]


def test_rank_candidates_as_of_skips_symbols_with_insufficient_history(tmp_path):
    old_bars = [
        HistoricalBar(date(2026, 1, 1), 100.0, 100.5, 99.5, 100.0),
        HistoricalBar(date(2026, 1, 2), 100.0, 100.5, 99.7, 100.1),
        HistoricalBar(date(2026, 1, 3), 100.1, 100.6, 99.8, 100.05),
    ]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            if symbol == "NEW":
                return [HistoricalBar(date(2026, 1, 3), 50.0, 51.0, 49.0, 50.0)]
            return [b for b in old_bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    ranked = backtest_commands.rank_candidates_as_of(
        ["OLD", "NEW"], store, date(2026, 1, 3), vol_window_days=2, atr_window_days=2,
    )

    assert ranked == ["OLD"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.backtest_commands' has no attribute 'rank_candidates_as_of'`

- [ ] **Step 3: Write minimal implementation**

Add this import line to `robinhood_bot/backtest_commands.py`, alongside the existing ones:

```python
from .universe import average_true_range_pct, percentile_ranks, realized_volatility
```

Then append:

```python
def rank_candidates_as_of(
    symbols: list[str],
    store: HistoricalPriceStore,
    today: date,
    vol_window_days: int = 20,
    atr_window_days: int = 14,
) -> list[str]:
    vols: dict[str, float] = {}
    atrs: dict[str, float] = {}

    for symbol in symbols:
        closes = store.get_closes_window(symbol, today, vol_window_days + 1)
        bars = store.get_ohlc_window(symbol, today, atr_window_days + 1)
        if len(closes) < 2 or len(bars) < 2:
            continue
        vols[symbol] = realized_volatility(closes)
        atrs[symbol] = average_true_range_pct(bars)

    vol_ranks = percentile_ranks(vols)
    atr_ranks = percentile_ranks(atrs)
    scored = {s: (vol_ranks[s] + atr_ranks[s]) / 2 for s in vols}
    return sorted(scored, key=lambda s: scored[s], reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: add as-of-day candidate re-ranking for the deterministic backtest"
```

---

### Task 8: The deterministic backtest loop (`cmd_backtest_run`)

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Modify: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `resolve_run_paths`, `rank_candidates_as_of` (Tasks 6, 7); `ledger.load_state`/`save_state` (existing); `commands.cmd_record_fill` (existing); `risk_engine.evaluate_position`/`evaluate_buy`/`max_new_position_value`/`ExitAction` (existing); `portfolio_state.roll_month_if_needed` (existing).
- Produces: `cmd_backtest_run(run_id, base_dir, starting_cash, start, end, candidate_symbols, store, cfg, benchmark_symbol="SPY", vol_window_days=20, atr_window_days=14) -> dict`. Writes `equity_curve.csv` (`date,cash,positions_value,total_equity`) once per simulated day, in addition to the usual ledger/trade-log side effects.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_commands.py — append
import csv


def test_cmd_backtest_run_executes_deterministic_entry_exit_cycle(tmp_path):
    bars = {
        "A": [
            HistoricalBar(date(2025, 12, 31), 98.0, 99.5, 97.5, 99.0),
            HistoricalBar(date(2026, 1, 2), 99.0, 100.5, 98.5, 100.0),
            HistoricalBar(date(2026, 1, 5), 106.0, 109.0, 105.0, 108.0),
        ],
        "SPY": [
            HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
            HistoricalBar(date(2026, 1, 5), 402.0, 404.0, 401.0, 403.0),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars[symbol] if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.05, profit_target_pct=0.08,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
    )

    result = backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 5),
        candidate_symbols=["A"], store=store, cfg=cfg,
    )

    assert result["trading_days"] == 2

    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    assert final_state.active_positions[0].symbol == "A"
    assert final_state.active_positions[0].qty == 48
    assert final_state.active_positions[0].entry_price == 108.0
    assert final_state.cash == pytest.approx(5_216.0)
    assert final_state.month == "2026-01"
    assert final_state.month_start_equity == pytest.approx(10_000.0)

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert [r["action"] for r in rows] == ["BUY", "SELL", "BUY"]

    with paths.equity_curve.open() as f:
        equity_rows = list(csv.DictReader(f))
    assert equity_rows[0]["date"] == "2026-01-02"
    assert float(equity_rows[0]["total_equity"]) == pytest.approx(10_000.0)
    assert equity_rows[1]["date"] == "2026-01-05"
    assert float(equity_rows[1]["total_equity"]) == pytest.approx(10_400.0)
```

This scenario: day 1 (2026-01-02) has one free slot and one candidate (`A`, $100 close) — `evaluate_buy` approves a $5,000 position (50% of $10,000 equity, since `max_position_pct == min_position_pct == 0.5`), buying 50 shares. Day 2 (2026-01-05), `A` closes at $108 — an 8% gain that exactly hits `profit_target_pct`, triggering a `SELL`; the freed slot immediately re-buys `A` at $108 (48 shares, `floor($5,200 / $108)`), since it's still the only (and therefore top-ranked) candidate.

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.backtest_commands' has no attribute 'cmd_backtest_run'`

- [ ] **Step 3: Write minimal implementation**

Replace the imports at the top of `robinhood_bot/backtest_commands.py` with:

```python
# robinhood_bot/backtest_commands.py
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import commands, ledger
from .backtest_data import HistoricalPriceStore
from .portfolio_state import roll_month_if_needed
from .risk_engine import ExitAction, RiskConfig, evaluate_buy, evaluate_position, max_new_position_value
from .universe import average_true_range_pct, percentile_ranks, realized_volatility
```

Then append:

```python
def _append_equity_curve(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "cash", "positions_value", "total_equity"])
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _total_equity(state, store: HistoricalPriceStore, today: date) -> tuple[float, float]:
    positions_value = sum(
        (store.get_close(p.symbol, today) or p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    return state.cash, positions_value


def cmd_backtest_run(
    run_id: str,
    base_dir: Path,
    starting_cash: float,
    start: date,
    end: date,
    candidate_symbols: list[str],
    store: HistoricalPriceStore,
    cfg: RiskConfig,
    benchmark_symbol: str = "SPY",
    vol_window_days: int = 20,
    atr_window_days: int = 14,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    trading_days = store.trading_days(benchmark_symbol, start, end)

    for today in trading_days:
        # 1. Exits: evaluate every active position against today's close.
        state = ledger.load_state(paths.ledger, starting_cash)
        remaining_active = []
        for position in state.active_positions:
            price = store.get_close(position.symbol, today)
            if price is None:
                remaining_active.append(position)
                continue
            evaluation = evaluate_position(position, price, today, cfg)
            if evaluation.action == ExitAction.SELL:
                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "sell", position.symbol,
                    position.qty, price, today, "backtest exit",
                )
                continue
            position.status = evaluation.new_status
            position.underwater_since = evaluation.new_underwater_since
            if evaluation.action == ExitAction.PROMOTE_LONG_HOLD:
                state.long_hold_positions.append(position)
            else:
                remaining_active.append(position)
        state.active_positions = remaining_active
        # `cmd_record_fill` above does its own independent load/save cycle against
        # the ledger file for each sell, so it already persisted the cash credit
        # for this sell on disk. Our in-memory `state.cash` is still the pre-sell
        # snapshot taken at the top of the loop, so pull the up-to-date cash back
        # in here before we overwrite the file, or we'd clobber every sell's cash
        # credit with the stale pre-sell balance.
        state.cash = ledger.load_state(paths.ledger, starting_cash).cash
        ledger.save_state(paths.ledger, state)

        # Roll the monthly circuit-breaker baseline exactly like `cmd_state` does,
        # since this loop never calls `cmd_state` itself.
        state = ledger.load_state(paths.ledger, starting_cash)
        cash, positions_value = _total_equity(state, store, today)
        roll_month_if_needed(state, today, cash + positions_value)
        ledger.save_state(paths.ledger, state)

        # 2. Entries: fill free slots with the top-ranked candidate not already held.
        free_slots = cfg.max_active_positions - state.active_slot_count()
        if free_slots > 0:
            held = {p.symbol for p in state.active_positions + state.long_hold_positions}
            ranked = rank_candidates_as_of(candidate_symbols, store, today, vol_window_days, atr_window_days)
            for symbol in ranked:
                if free_slots <= 0:
                    break
                if symbol in held:
                    continue
                price = store.get_close(symbol, today)
                if price is None:
                    continue

                cash, positions_value = _total_equity(state, store, today)
                total_equity = cash + positions_value
                max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)
                proposed_value = min(max_value, state.cash)
                decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg)
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry",
                )
                state = ledger.load_state(paths.ledger, starting_cash)
                held.add(symbol)
                free_slots -= 1

        state = ledger.load_state(paths.ledger, starting_cash)
        cash, positions_value = _total_equity(state, store, today)
        _append_equity_curve(paths.equity_curve, {
            "date": today.isoformat(),
            "cash": cash,
            "positions_value": positions_value,
            "total_equity": cash + positions_value,
        })

    return {
        "run_id": run_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "trading_days": len(trading_days),
    }
```

**Note:** `rank_candidates_as_of` must already be defined above this function in the file (Task 7) since `cmd_backtest_run` calls it directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: add deterministic backtest loop with equity curve tracking"
```

---

### Task 9: Backtest reporting (`cmd_backtest_report`)

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Modify: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `resolve_run_paths` (Task 6); `equity_curve.csv`/`trade_log.csv` written by `cmd_backtest_run` (Task 8); `HistoricalPriceStore.get_close` (Task 2).
- Produces: `cmd_backtest_report(run_id, base_dir, store, benchmark_symbol="SPY") -> dict` with keys `run_id`, `start`, `end`, `starting_equity`, `ending_equity`, `total_return_pct`, `max_drawdown_pct`, `wins`, `losses`, `benchmark_symbol`, `benchmark_return_pct`. Raises `ValueError` if the run has no `equity_curve.csv` yet (i.e. `backtest run` hasn't been executed for it).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_commands.py — append
def test_cmd_backtest_report_computes_return_and_benchmark(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    paths.equity_curve.parent.mkdir(parents=True, exist_ok=True)
    with paths.equity_curve.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "cash", "positions_value", "total_equity"])
        writer.writeheader()
        writer.writerow({"date": "2026-01-02", "cash": 5000.0, "positions_value": 5000.0, "total_equity": 10000.0})
        writer.writerow({"date": "2026-01-05", "cash": 5216.0, "positions_value": 5184.0, "total_equity": 10400.0})
    with paths.trade_log.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "action", "symbol", "qty", "price", "reason"])
        writer.writeheader()
        writer.writerow({"timestamp": "2026-01-02", "action": "BUY", "symbol": "A", "qty": 50, "price": 100.0, "reason": "entry"})
        writer.writerow({"timestamp": "2026-01-05", "action": "SELL", "symbol": "A", "qty": 50, "price": 108.0, "reason": "exit"})

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                HistoricalBar(date(2026, 1, 5), 402.0, 404.0, 401.0, 404.0),
            ]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_report("run1", tmp_path, store)

    assert result["starting_equity"] == 10_000.0
    assert result["ending_equity"] == 10_400.0
    assert result["total_return_pct"] == pytest.approx(0.04)
    assert result["max_drawdown_pct"] == 0.0
    assert result["wins"] == 1
    assert result["losses"] == 0
    assert result["benchmark_return_pct"] == pytest.approx(0.01)


def test_cmd_backtest_report_computes_max_drawdown(tmp_path):
    paths = backtest_commands.resolve_run_paths("run2", tmp_path)
    paths.equity_curve.parent.mkdir(parents=True, exist_ok=True)
    with paths.equity_curve.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "cash", "positions_value", "total_equity"])
        writer.writeheader()
        writer.writerow({"date": "2026-01-02", "cash": 10000.0, "positions_value": 0.0, "total_equity": 10000.0})
        writer.writerow({"date": "2026-01-05", "cash": 9000.0, "positions_value": 0.0, "total_equity": 9000.0})
        writer.writerow({"date": "2026-01-06", "cash": 9500.0, "positions_value": 0.0, "total_equity": 9500.0})

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                HistoricalBar(date(2026, 1, 6), 400.0, 401.0, 399.0, 400.0),
            ]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_report("run2", tmp_path, store)

    assert result["max_drawdown_pct"] == pytest.approx(0.10)
    assert result["wins"] == 0
    assert result["losses"] == 0


def test_cmd_backtest_report_raises_when_no_equity_curve(tmp_path):
    with pytest.raises(ValueError):
        backtest_commands.cmd_backtest_report("missing-run", tmp_path, store=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.backtest_commands' has no attribute 'cmd_backtest_report'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/backtest_commands.py`:

```python
def cmd_backtest_report(
    run_id: str, base_dir: Path, store: HistoricalPriceStore, benchmark_symbol: str = "SPY",
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)

    if not paths.equity_curve.exists():
        raise ValueError(f"no equity curve data for run {run_id!r} — has `backtest run` been executed?")

    with paths.equity_curve.open() as f:
        equity_rows = list(csv.DictReader(f))

    starting_equity = float(equity_rows[0]["total_equity"])
    ending_equity = float(equity_rows[-1]["total_equity"])
    total_return_pct = ending_equity / starting_equity - 1.0

    peak = starting_equity
    max_drawdown_pct = 0.0
    for row in equity_rows:
        equity = float(row["total_equity"])
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

    wins = 0
    losses = 0
    if paths.trade_log.exists():
        with paths.trade_log.open() as f:
            trade_rows = list(csv.DictReader(f))
        open_buys: dict[str, dict] = {}
        for row in trade_rows:
            if row["action"] == "BUY":
                open_buys[row["symbol"]] = row
            elif row["action"] == "SELL":
                buy_row = open_buys.pop(row["symbol"], None)
                if buy_row is None:
                    continue
                pnl = (float(row["price"]) - float(buy_row["price"])) * float(row["qty"])
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1

    start_date = date.fromisoformat(equity_rows[0]["date"])
    end_date = date.fromisoformat(equity_rows[-1]["date"])
    benchmark_start = store.get_close(benchmark_symbol, start_date)
    benchmark_end = store.get_close(benchmark_symbol, end_date)
    benchmark_return_pct = (
        (benchmark_end / benchmark_start - 1.0) if benchmark_start and benchmark_end else None
    )

    return {
        "run_id": run_id,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "wins": wins,
        "losses": losses,
        "benchmark_symbol": benchmark_symbol,
        "benchmark_return_pct": benchmark_return_pct,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: add backtest report with return, drawdown, and benchmark comparison"
```

---

### Task 10: `cli.py backtest` subcommand group

**Files:**
- Modify: `robinhood_bot/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from `backtest_commands.py` (Tasks 6-9); `HistoricalPriceStore` (Task 2); `LiveHistoricalDataFetcher` (Task 5); `build_universe`, `UniverseConfig` (existing); `LiveMarketDataClient` (existing).
- Produces: a new `backtest` subcommand on `cli.main` with sub-subcommands `state`, `quote`, `risk-check`, `record-fill`, `check-stop-losses`, `run`, `report`, `trading-days`, matching the spec's CLI surface (plus the `--prices-json` addition to `state` noted in Global Constraints).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (add `from datetime import date` and `from robinhood_bot import backtest_data` to the imports):

```python
from datetime import date

from robinhood_bot import backtest_data


def test_cli_backtest_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    exit_code = cli.main(["backtest", "state", "--run", "run1", "--asof", "2026-01-05"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_mode"] == "backtest"
    assert output["cash"] == cli.STARTING_CASH


def test_cli_backtest_quote_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path)

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [backtest_data.HistoricalBar(date(2026, 1, 5), 100.0, 101.0, 99.0, 100.5)]

    monkeypatch.setattr(cli, "LiveHistoricalDataFetcher", FakeFetcher)

    exit_code = cli.main(["backtest", "quote", "AAPL", "--asof", "2026-01-05"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["symbol"] == "AAPL"
    assert output["price"] == 100.5


def test_cli_backtest_trading_days_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path)

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                backtest_data.HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                backtest_data.HistoricalBar(date(2026, 1, 5), 402.0, 403.0, 401.0, 402.0),
            ]

    monkeypatch.setattr(cli, "LiveHistoricalDataFetcher", FakeFetcher)

    exit_code = cli.main(["backtest", "trading-days", "--start", "2026-01-01", "--end", "2026-01-06"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_days"] == ["2026-01-02", "2026-01-05"]


def test_cli_backtest_record_fill_command_writes_isolated_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    exit_code = cli.main([
        "backtest", "record-fill", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--qty", "5", "--price", "300.0", "--reason", "test",
    ])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["cash"] == cli.STARTING_CASH - 1_500.0
    assert (tmp_path / "run1" / "ledger.json").exists()


def test_cli_backtest_run_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    fake_candidates = [universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0)]
    monkeypatch.setattr(cli, "build_universe", lambda client, cache_path, cfg, today: fake_candidates)

    captured = {}

    def fake_cmd_backtest_run(run_id, base_dir, starting_cash, start, end, candidate_symbols, store, cfg, benchmark_symbol):
        captured["candidate_symbols"] = candidate_symbols
        return {"run_id": run_id, "trading_days": 0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_run", fake_cmd_backtest_run)

    exit_code = cli.main(["backtest", "run", "--run", "run1", "--start", "2026-01-01", "--end", "2026-01-05"])

    assert exit_code == 0
    assert captured["candidate_symbols"] == ["AAPL"]
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run1"


def test_cli_backtest_report_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    def fake_cmd_backtest_report(run_id, base_dir, store, benchmark_symbol):
        return {"run_id": run_id, "total_return_pct": 0.05}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_report", fake_cmd_backtest_report)

    exit_code = cli.main(["backtest", "report", "--run", "run1"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["total_return_pct"] == 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.cli' has no attribute 'BACKTEST_BASE_DIR'`

- [ ] **Step 3: Write the implementation**

Replace the full contents of `robinhood_bot/cli.py` with:

```python
# robinhood_bot/cli.py
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import backtest_commands, commands
from .backtest_data import HistoricalPriceStore
from .risk_engine import RiskConfig
from .universe import UniverseConfig, build_universe
from .universe_client import LiveHistoricalDataFetcher, LiveMarketDataClient

LEDGER_PATH = Path("data/ledger.json")
TRADE_LOG_PATH = Path("data/trade_log.csv")
UNIVERSE_CACHE_PATH = Path("data/universe_cache.json")
BACKTEST_BASE_DIR = Path("data/backtests")
HISTORICAL_CACHE_DIR = Path("data/historical_price_cache")
STARTING_CASH = 10_000.0
TRADING_MODE = "paper"
BENCHMARK_SYMBOL = "SPY"


def _parse_prices(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    return json.loads(raw)


def _build_price_store() -> HistoricalPriceStore:
    return HistoricalPriceStore(LiveHistoricalDataFetcher(), HISTORICAL_CACHE_DIR)


def _dispatch_backtest(args) -> dict:
    cfg = RiskConfig()

    if args.backtest_command == "state":
        return backtest_commands.cmd_backtest_state(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof),
        )
    if args.backtest_command == "quote":
        return backtest_commands.cmd_backtest_quote(
            args.symbol, date.fromisoformat(args.asof), _build_price_store(),
        )
    if args.backtest_command == "risk-check":
        return backtest_commands.cmd_backtest_risk_check(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg,
        )
    if args.backtest_command == "record-fill":
        return backtest_commands.cmd_backtest_record_fill(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, date.fromisoformat(args.asof), args.reason,
        )
    if args.backtest_command == "check-stop-losses":
        return backtest_commands.cmd_backtest_check_stop_losses(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof), cfg, args.apply,
        )
    if args.backtest_command == "trading-days":
        return backtest_commands.cmd_backtest_trading_days(
            date.fromisoformat(args.start), date.fromisoformat(args.end), _build_price_store(),
            BENCHMARK_SYMBOL,
        )
    if args.backtest_command == "run":
        store = _build_price_store()
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, UniverseConfig(), date.today(),
        )
        return backtest_commands.cmd_backtest_run(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, date.fromisoformat(args.start),
            date.fromisoformat(args.end), [c.symbol for c in candidates], store, cfg,
            BENCHMARK_SYMBOL,
        )
    if args.backtest_command == "report":
        return backtest_commands.cmd_backtest_report(
            args.run, BACKTEST_BASE_DIR, _build_price_store(), BENCHMARK_SYMBOL,
        )

    raise ValueError(f"unknown backtest command: {args.backtest_command}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robinhood_bot.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("state").add_argument("--prices-json", default=None)

    p_risk = sub.add_parser("risk-check")
    p_risk.add_argument("action", choices=["buy", "sell"])
    p_risk.add_argument("symbol")
    p_risk.add_argument("--value", type=float, default=0.0)
    p_risk.add_argument("--prices-json", default=None)

    p_fill = sub.add_parser("record-fill")
    p_fill.add_argument("action", choices=["buy", "sell"])
    p_fill.add_argument("symbol")
    p_fill.add_argument("--qty", type=float, required=True)
    p_fill.add_argument("--price", type=float, required=True)
    p_fill.add_argument("--reason", default="")

    p_stop = sub.add_parser("check-stop-losses")
    p_stop.add_argument("--prices-json", required=True)
    p_stop.add_argument("--apply", action="store_true")

    p_universe = sub.add_parser("universe")
    p_universe.add_argument("--refresh", action="store_true")
    p_universe.add_argument("--mode", choices=["realized_vol", "atr_pct", "both"], default=None)

    p_backtest = sub.add_parser("backtest")
    backtest_sub = p_backtest.add_subparsers(dest="backtest_command", required=True)

    p_bt_state = backtest_sub.add_parser("state")
    p_bt_state.add_argument("--run", required=True)
    p_bt_state.add_argument("--asof", required=True)
    p_bt_state.add_argument("--prices-json", default=None)

    p_bt_quote = backtest_sub.add_parser("quote")
    p_bt_quote.add_argument("symbol")
    p_bt_quote.add_argument("--asof", required=True)

    p_bt_risk = backtest_sub.add_parser("risk-check")
    p_bt_risk.add_argument("action", choices=["buy", "sell"])
    p_bt_risk.add_argument("symbol")
    p_bt_risk.add_argument("--run", required=True)
    p_bt_risk.add_argument("--asof", required=True)
    p_bt_risk.add_argument("--value", type=float, default=0.0)
    p_bt_risk.add_argument("--prices-json", default=None)

    p_bt_fill = backtest_sub.add_parser("record-fill")
    p_bt_fill.add_argument("action", choices=["buy", "sell"])
    p_bt_fill.add_argument("symbol")
    p_bt_fill.add_argument("--run", required=True)
    p_bt_fill.add_argument("--asof", required=True)
    p_bt_fill.add_argument("--qty", type=float, required=True)
    p_bt_fill.add_argument("--price", type=float, required=True)
    p_bt_fill.add_argument("--reason", default="")

    p_bt_stop = backtest_sub.add_parser("check-stop-losses")
    p_bt_stop.add_argument("--run", required=True)
    p_bt_stop.add_argument("--asof", required=True)
    p_bt_stop.add_argument("--prices-json", required=True)
    p_bt_stop.add_argument("--apply", action="store_true")

    p_bt_run = backtest_sub.add_parser("run")
    p_bt_run.add_argument("--run", required=True)
    p_bt_run.add_argument("--start", required=True)
    p_bt_run.add_argument("--end", required=True)

    p_bt_report = backtest_sub.add_parser("report")
    p_bt_report.add_argument("--run", required=True)

    p_bt_days = backtest_sub.add_parser("trading-days")
    p_bt_days.add_argument("--start", required=True)
    p_bt_days.add_argument("--end", required=True)

    args = parser.parse_args(argv)
    today = date.today()
    cfg = RiskConfig()

    if args.command == "state":
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE
        )
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason,
        )
    elif args.command == "check-stop-losses":
        result = commands.cmd_check_stop_losses(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, cfg, args.apply,
        )
    elif args.command == "backtest":
        result = _dispatch_backtest(args)
    else:
        universe_cfg = UniverseConfig()
        if args.mode:
            universe_cfg.ranking_mode = args.mode
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, universe_cfg, today, args.refresh
        )
        result = {
            "candidates": [
                {
                    "symbol": c.symbol,
                    "category": c.category,
                    "market_cap": c.market_cap,
                    "realized_vol": c.realized_vol,
                    "atr_pct": c.atr_pct,
                    "combined_rank": c.combined_rank,
                }
                for c in candidates
            ]
        }

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (119 tests total: 87 existing + 12 in `test_backtest_data.py` + 14 in `test_backtest_commands.py` + 6 new in `test_cli.py`)

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: add backtest subcommand group to cli.py"
```

---

### Task 11: Backtest Mode section on the `robinhood-trading` skill

**Files:**
- Modify: `.claude/skills/robinhood-trading/SKILL.md`

**Interfaces:**
- Consumes: `cli.py backtest state/quote/risk-check/record-fill/trading-days` (Task 10).
- Produces: a new `## Backtest Mode` section, documenting how Claude runs the same research-and-decide loop from the existing skill day-by-day over a historical range instead of live data. No automated test — per the design spec's testing strategy, this is verified by actually running it (Task 12 covers the Python side only; a full LLM-mode dry run is a follow-up, not blocking this plan).

- [ ] **Step 1: Append the new section**

Append to the end of `.claude/skills/robinhood-trading/SKILL.md`:

```markdown
## Backtest Mode

Invoked explicitly with a date range and run id, e.g. `/robinhood-trading
--backtest --run RUN_ID --start 2026-01-01 --end 2026-03-31`. Runs the same
research-and-decide loop as the daily cycle above, once per simulated
trading day, entirely against historical data — no Robinhood MCP
connection is used or needed in this mode.

### Get the list of simulated days

```
python -m robinhood_bot.cli backtest trading-days --start START_DATE --end END_DATE
```

Loop through each date in the returned `trading_days` list, in order,
running Steps 1-9 below for each one before moving to the next simulated
date.

### Per-simulated-day steps

Replace the live commands from the daily cycle above with their `backtest`
equivalents, all parameterized by `--run RUN_ID --asof <simulated date>`:

- **Step 1 (read mode & holdings):** `python -m robinhood_bot.cli backtest
  state --run RUN_ID --asof <simulated date> --prices-json "{}"`. Note that
  `trading_mode` here is always `"backtest"` — there is no live-order-
  placement branch anywhere in this mode; every trade is simulated.
- **Step 2 (universe):** skipped — `backtest run`'s candidate list (today's
  live universe, applied retroactively) isn't available per-command in
  this mode. Instead, shortlist from whatever symbols you already know are
  liquid, well-known equities (e.g. run `cli.py universe` once, live,
  before starting the backtest, and reuse that fixed candidate list for
  every simulated day — mirroring exactly what `backtest run`'s
  deterministic mode does internally).
- **Step 4 (fresh quotes):** `python -m robinhood_bot.cli backtest quote
  SYMBOL --asof <simulated date>` for each shortlisted symbol, in place of
  the Robinhood MCP quote tool. If `"price"` comes back `null`, skip that
  symbol for this simulated day — same rule as a failed live quote.
- **Step 5 (refresh state with real prices):** `python -m robinhood_bot.cli
  backtest state --run RUN_ID --asof <simulated date> --prices-json
  "<quotes from Step 4>"`.
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --value <proposed dollar amount, for buys> --prices-json "<quotes>"`,
  then on approval, `python -m robinhood_bot.cli
  backtest record-fill {buy|sell} SYMBOL --run RUN_ID --asof <simulated
  date> --qty <n> --price <quote price> --reason "<why>"`. There is no
  live-order-placement call in this mode, ever.

### Summarize

After the last simulated day, run:

```
python -m robinhood_bot.cli backtest report --run RUN_ID
```

Report `total_return_pct`, `max_drawdown_pct`, `wins`/`losses`, and
`benchmark_return_pct` (buy-and-hold SPY over the same window) to the
user, alongside any symbols that were skipped for missing quotes.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md
git commit -m "docs: add Backtest Mode to the robinhood-trading skill"
```

---

### Task 12: `.gitignore` update and manual end-to-end verification

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `cli.py backtest run`/`report` (Task 10).
- Produces: no new code — this is the design spec's required manual verification ("one real `cli.py backtest run` against a short live date range... to confirm the actual `yfinance` historical fetch and caching work end to end"), plus the `.gitignore` entries needed before that run touches real files under `data/`.

- [ ] **Step 1: Update `.gitignore`**

Change `.gitignore` from:

```
.venv/
__pycache__/
*.pyc
data/*.csv
data/*.json
.pytest_cache/
```

to:

```
.venv/
__pycache__/
*.pyc
data/*.csv
data/*.json
data/backtests/
data/historical_price_cache/
.pytest_cache/
```

(`data/*.csv`/`data/*.json` only match files directly inside `data/`, not the nested `data/backtests/<run_id>/*.json` or `data/historical_price_cache/*.json` paths this plan introduces.)

- [ ] **Step 2: Run a real short-range backtest**

```bash
D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m robinhood_bot.cli backtest run --run manual-verify --start 2026-06-19 --end 2026-07-18
```

Expected: prints a JSON object with `"run_id": "manual-verify"` and a non-zero `"trading_days"` count (roughly 20-22 trading days in a one-month window). This is the first call to touch the real network (`yfinance`) and real disk paths (`data/backtests/manual-verify/`, `data/historical_price_cache/`) — confirm both were created:

```bash
ls data/backtests/manual-verify/
ls data/historical_price_cache/ | head -5
```

- [ ] **Step 3: Run the report**

```bash
D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m robinhood_bot.cli backtest report --run manual-verify
```

Expected: a JSON object with `starting_equity`, `ending_equity`, `total_return_pct`, `max_drawdown_pct`, `wins`, `losses`, and a non-null `benchmark_return_pct`. Sanity-check that `starting_equity` is close to `10000.0` and the numbers are internally consistent (e.g. `ending_equity` roughly matches `starting_equity * (1 + total_return_pct)`).

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore backtest run artifacts and historical price cache"
```

Note any discrepancy from the expected behavior above (a stale Wikipedia table structure, an unexpected `yfinance` error, an empty candidate list) in your final report rather than silently working around it.

---

## What This Plan Does Not Cover

- Point-in-time historical index membership — the backtest always trades within *today's* live universe snapshot, applied retroactively (survivorship bias, explicitly accepted per the design spec's non-goals).
- Multiple pluggable deterministic strategies — one fixed rule (top combined-rank candidate fills each free slot).
- Scheduled/automated invocation of either backtest mode.
- Options/crypto/futures backtesting.
- A full, actually-executed LLM-driven backtest dry run (Task 11 only adds the skill documentation for it) — worth doing once as a follow-up, but it costs one reasoning pass per simulated day and isn't required to validate the Python engine this plan builds.

All of the above are separate, later design/planning efforts.
