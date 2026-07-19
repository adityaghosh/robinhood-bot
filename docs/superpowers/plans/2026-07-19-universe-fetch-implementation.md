# Universe Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `universe.py` — the module that ranks the daily-cycle's tradeable candidates (top ~100 S&P 500 + top ~20 Nasdaq-100 by market cap, plus a fixed leveraged-fund list) by recent volatility, with a weekly-cached membership tier and a network client isolated behind an injectable interface so the ranking logic is fully unit-tested with zero network calls.

**Architecture:** `universe.py` holds pure data models, cache (de)serialization, volatility/ranking math, and orchestration functions (`get_membership`, `build_universe`) that take a `MarketDataClient` Protocol as a parameter — never touching the network directly. `universe_client.py` holds the one class permitted to make real network calls: `LiveMarketDataClient`, which scrapes Wikipedia's index-membership tables and calls `yfinance` for market caps and historical bars. `cli.py` gains a fifth subcommand, `universe`, wiring the two together.

**Tech Stack:** Python 3.11+, `pandas` + `lxml` (new dependency, for `pd.read_html`) for Wikipedia scraping, `yfinance` for market data — both already partial dependencies from the original scaffold, `pytest` for testing.

## Global Constraints

- `universe.py` itself stays network-free and fully unit-testable: every function that needs external data takes a `MarketDataClient` (or an already-fetched value) as a parameter — it never imports `yfinance` or calls `pandas.read_html` directly.
- All real network I/O is isolated in `universe_client.py`'s `LiveMarketDataClient` — the only file in this plan that touches the network.
- New dependency: `lxml>=5.0` (required by `pandas.read_html`'s default parser) — add to `requirements.txt`.
- `data/universe_cache.json` is already covered by the existing `.gitignore` pattern `data/*.json` from Plan 1 — no gitignore change needed.
- A missing or failed data fetch is never silently estimated: a symbol with no historical bars is dropped from the ranked output, not scored with a fabricated value; a membership refresh failure falls back to the existing cache if one exists, and fails loudly (raises) if there is no cache to fall back to.
- Every task ends green (`pytest` passing) before moving to the next, except Task 10, whose `LiveMarketDataClient` network methods are verified manually per the design spec's testing strategy — only its one pure helper function is pytest-covered.

---

### Task 1: Data models and the `MarketDataClient` Protocol

**Files:**
- Create: `robinhood_bot/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Produces: `UniverseConfig(top_n_sp500: int = 100, top_n_nasdaq100: int = 20, leveraged_funds: list[str] = ["TQQQ", "UPRO", "SOXL"], realized_vol_window_days: int = 20, atr_window_days: int = 14, cache_max_age_days: int = 7, ranking_mode: str = "both")`; `Bar(high: float, low: float, close: float)`; `CachedMember(symbol: str, category: str, market_cap: float)`; `UniverseCache(fetched_at: date, members: list[CachedMember])`; `Candidate(symbol: str, category: str, market_cap: float, realized_vol: float, atr_pct: float, combined_rank: float)`; `MarketDataClient` Protocol with `fetch_sp500_tickers() -> list[str]`, `fetch_nasdaq100_tickers() -> list[str]`, `fetch_market_caps(tickers: list[str]) -> dict[str, float]`, `fetch_daily_bars(ticker: str, lookback_days: int) -> list[Bar]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_universe.py
from datetime import date

from robinhood_bot.universe import (
    Bar,
    CachedMember,
    Candidate,
    UniverseCache,
    UniverseConfig,
)


def test_universe_config_defaults():
    cfg = UniverseConfig()
    assert cfg.top_n_sp500 == 100
    assert cfg.top_n_nasdaq100 == 20
    assert cfg.leveraged_funds == ["TQQQ", "UPRO", "SOXL"]
    assert cfg.realized_vol_window_days == 20
    assert cfg.atr_window_days == 14
    assert cfg.cache_max_age_days == 7
    assert cfg.ranking_mode == "both"


def test_bar_fields():
    bar = Bar(high=101.0, low=99.0, close=100.0)
    assert bar.high == 101.0
    assert bar.low == 99.0
    assert bar.close == 100.0


def test_cached_member_fields():
    member = CachedMember(symbol="AAPL", category="sp500", market_cap=3.0e12)
    assert member.symbol == "AAPL"
    assert member.category == "sp500"
    assert member.market_cap == 3.0e12


def test_universe_cache_fields():
    cache = UniverseCache(
        fetched_at=date(2026, 7, 19),
        members=[CachedMember("AAPL", "sp500", 3.0e12)],
    )
    assert cache.fetched_at == date(2026, 7, 19)
    assert cache.members[0].symbol == "AAPL"


def test_candidate_fields():
    candidate = Candidate(
        symbol="AAPL", category="sp500", market_cap=3.0e12,
        realized_vol=0.25, atr_pct=0.02, combined_rank=0.9,
    )
    assert candidate.symbol == "AAPL"
    assert candidate.combined_rank == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.universe'`

- [ ] **Step 3: Write minimal implementation**

```python
# robinhood_bot/universe.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class UniverseConfig:
    top_n_sp500: int = 100
    top_n_nasdaq100: int = 20
    leveraged_funds: list[str] = field(default_factory=lambda: ["TQQQ", "UPRO", "SOXL"])
    realized_vol_window_days: int = 20
    atr_window_days: int = 14
    cache_max_age_days: int = 7
    ranking_mode: str = "both"


@dataclass
class Bar:
    high: float
    low: float
    close: float


@dataclass
class CachedMember:
    symbol: str
    category: str
    market_cap: float


@dataclass
class UniverseCache:
    fetched_at: date
    members: list[CachedMember]


@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    realized_vol: float
    atr_pct: float
    combined_rank: float


class MarketDataClient(Protocol):
    def fetch_sp500_tickers(self) -> list[str]: ...
    def fetch_nasdaq100_tickers(self) -> list[str]: ...
    def fetch_market_caps(self, tickers: list[str]) -> dict[str, float]: ...
    def fetch_daily_bars(self, ticker: str, lookback_days: int) -> list[Bar]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add universe data models and MarketDataClient protocol"
```

---

### Task 2: Cache serialization

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Consumes: `CachedMember`, `UniverseCache` (Task 1).
- Produces: `cache_to_dict(cache: UniverseCache) -> dict`; `cache_from_dict(data: dict) -> UniverseCache`; `load_cache(path: Path) -> UniverseCache | None`; `save_cache(path: Path, cache: UniverseCache) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py` (add `from pathlib import Path` to the imports):

```python
from pathlib import Path

from robinhood_bot.universe import load_cache, save_cache


def test_load_cache_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "universe_cache.json"
    assert load_cache(path) is None


def test_save_and_load_cache_round_trip(tmp_path):
    path = tmp_path / "universe_cache.json"
    original = UniverseCache(
        fetched_at=date(2026, 7, 19),
        members=[
            CachedMember("AAPL", "sp500", 3.0e12),
            CachedMember("TQQQ", "leveraged", 0.0),
        ],
    )
    save_cache(path, original)
    loaded = load_cache(path)

    assert loaded.fetched_at == date(2026, 7, 19)
    assert loaded.members[0].symbol == "AAPL"
    assert loaded.members[0].market_cap == 3.0e12
    assert loaded.members[1].symbol == "TQQQ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_cache'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py` (add `import json` and `from pathlib import Path` to the imports):

```python
import json
from pathlib import Path


def _cached_member_to_dict(member: CachedMember) -> dict:
    return {"symbol": member.symbol, "category": member.category, "market_cap": member.market_cap}


def _cached_member_from_dict(data: dict) -> CachedMember:
    return CachedMember(symbol=data["symbol"], category=data["category"], market_cap=data["market_cap"])


def cache_to_dict(cache: UniverseCache) -> dict:
    return {
        "fetched_at": cache.fetched_at.isoformat(),
        "members": [_cached_member_to_dict(m) for m in cache.members],
    }


def cache_from_dict(data: dict) -> UniverseCache:
    return UniverseCache(
        fetched_at=date.fromisoformat(data["fetched_at"]),
        members=[_cached_member_from_dict(m) for m in data["members"]],
    )


def load_cache(path: Path) -> UniverseCache | None:
    if not path.exists():
        return None
    with path.open("r") as f:
        return cache_from_dict(json.load(f))


def save_cache(path: Path, cache: UniverseCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(cache_to_dict(cache), f, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add universe cache serialization"
```

---

### Task 3: Cache staleness

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Consumes: `UniverseCache` (Task 1).
- Produces: `is_cache_stale(cache: UniverseCache | None, today: date, max_age_days: int) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py` (add `from datetime import timedelta` to the imports):

```python
from datetime import timedelta

from robinhood_bot.universe import is_cache_stale


def test_is_cache_stale_when_cache_is_none():
    assert is_cache_stale(None, today=date(2026, 7, 19), max_age_days=7) is True


def test_is_cache_stale_at_exact_max_age_is_not_stale():
    cache = UniverseCache(fetched_at=date(2026, 7, 12), members=[])
    assert is_cache_stale(cache, today=date(2026, 7, 19), max_age_days=7) is False


def test_is_cache_stale_past_max_age_is_stale():
    cache = UniverseCache(fetched_at=date(2026, 7, 11), members=[])
    assert is_cache_stale(cache, today=date(2026, 7, 19), max_age_days=7) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_cache_stale'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py`:

```python
def is_cache_stale(cache: UniverseCache | None, today: date, max_age_days: int) -> bool:
    if cache is None:
        return True
    return (today - cache.fetched_at).days > max_age_days
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add universe cache staleness check"
```

---

### Task 4: Market-cap ranking

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Produces: `rank_top_by_market_cap(tickers: list[str], market_caps: dict[str, float], top_n: int) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py`:

```python
from robinhood_bot.universe import rank_top_by_market_cap


def test_rank_top_by_market_cap_orders_descending_and_truncates():
    tickers = ["A", "B", "C"]
    market_caps = {"A": 100.0, "B": 300.0, "C": 200.0}
    assert rank_top_by_market_cap(tickers, market_caps, top_n=2) == ["B", "C"]


def test_rank_top_by_market_cap_excludes_tickers_without_market_cap():
    tickers = ["A", "B", "D"]
    market_caps = {"A": 100.0, "B": 300.0}
    assert rank_top_by_market_cap(tickers, market_caps, top_n=5) == ["B", "A"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'rank_top_by_market_cap'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py`:

```python
def rank_top_by_market_cap(tickers: list[str], market_caps: dict[str, float], top_n: int) -> list[str]:
    known = [t for t in tickers if t in market_caps]
    known.sort(key=lambda t: market_caps[t], reverse=True)
    return known[:top_n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add market-cap ranking for universe candidates"
```

---

### Task 5: Realized volatility

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Produces: `realized_volatility(closes: list[float]) -> float`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py` (add `import pytest` to the imports):

```python
import pytest

from robinhood_bot.universe import realized_volatility


def test_realized_volatility_of_constant_closes_is_zero():
    assert realized_volatility([100.0, 100.0, 100.0, 100.0]) == 0.0


def test_realized_volatility_too_few_points_is_zero():
    assert realized_volatility([100.0]) == 0.0
    assert realized_volatility([]) == 0.0


def test_realized_volatility_known_value():
    closes = [100.0, 102.0, 98.0, 101.0, 99.0]
    assert realized_volatility(closes) == pytest.approx(0.5246239382982052)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'realized_volatility'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py` (add `import math` to the imports):

```python
import math


def realized_volatility(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add annualized realized volatility calculation"
```

---

### Task 6: Average True Range %

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Consumes: `Bar` (Task 1).
- Produces: `average_true_range_pct(bars: list[Bar]) -> float`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py`:

```python
from robinhood_bot.universe import average_true_range_pct


def test_average_true_range_pct_too_few_bars_is_zero():
    assert average_true_range_pct([]) == 0.0
    assert average_true_range_pct([Bar(101.0, 99.0, 100.0)]) == 0.0


def test_average_true_range_pct_known_value():
    bars = [
        Bar(high=101.0, low=99.0, close=100.0),
        Bar(high=103.0, low=100.0, close=102.0),
        Bar(high=102.5, low=99.5, close=101.0),
        Bar(high=104.0, low=100.5, close=103.0),
    ]
    assert average_true_range_pct(bars) == pytest.approx(0.030744336569579287)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'average_true_range_pct'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py`:

```python
def average_true_range_pct(bars: list[Bar]) -> float:
    if len(bars) < 2:
        return 0.0
    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(true_ranges) / len(true_ranges)
    last_close = bars[-1].close
    return (atr / last_close) if last_close else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add average true range percentage calculation"
```

---

### Task 7: Percentile ranking

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Produces: `percentile_ranks(values: dict[str, float]) -> dict[str, float]` — returns each key's rank in `[0.0, 1.0]`, ascending (lowest value → `0.0`, highest → `1.0`); a single-entry input maps to `1.0`; an empty input returns `{}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py`:

```python
from robinhood_bot.universe import percentile_ranks


def test_percentile_ranks_empty_input():
    assert percentile_ranks({}) == {}


def test_percentile_ranks_single_entry_is_one():
    assert percentile_ranks({"A": 5.0}) == {"A": 1.0}


def test_percentile_ranks_orders_ascending():
    result = percentile_ranks({"A": 1.0, "B": 3.0, "C": 2.0})
    assert result == {"A": 0.0, "C": 0.5, "B": 1.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'percentile_ranks'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py`:

```python
def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda s: values[s])
    n = len(ordered)
    if n == 1:
        return {ordered[0]: 1.0}
    return {symbol: i / (n - 1) for i, symbol in enumerate(ordered)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add percentile ranking for candidate scoring"
```

---

### Task 8: Membership refresh, dedup, and cache-aware fetch

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Consumes: `MarketDataClient`, `UniverseConfig`, `CachedMember`, `UniverseCache` (Task 1); `load_cache`, `save_cache` (Task 2); `is_cache_stale` (Task 3); `rank_top_by_market_cap` (Task 4).
- Produces: `refresh_membership(client: MarketDataClient, cfg: UniverseConfig) -> list[CachedMember]`; `get_membership(client: MarketDataClient, cache_path: Path, cfg: UniverseConfig, today: date, force_refresh: bool = False) -> list[CachedMember]`.

**Design note:** a ticker can legitimately be in both the top-100-S&P-500 and top-20-Nasdaq-100 lists (most of the largest Nasdaq-100 names — AAPL, MSFT, NVDA, etc. — are also top S&P 500 names by market cap). `refresh_membership` must dedupe by symbol so such a ticker appears exactly once in the output, not twice. S&P 500 membership is processed first, so on overlap the ticker keeps category `"sp500"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py`:

```python
from robinhood_bot.universe import get_membership, refresh_membership


class FakeMarketDataClient:
    def __init__(self, sp500=None, nasdaq100=None, market_caps=None, bars=None, raise_on_fetch=False):
        self.sp500 = sp500 or []
        self.nasdaq100 = nasdaq100 or []
        self.market_caps = market_caps or {}
        self.bars = bars or {}
        self.raise_on_fetch = raise_on_fetch
        self.calls = []

    def fetch_sp500_tickers(self):
        self.calls.append("sp500")
        if self.raise_on_fetch:
            raise RuntimeError("network error")
        return self.sp500

    def fetch_nasdaq100_tickers(self):
        self.calls.append("nasdaq100")
        if self.raise_on_fetch:
            raise RuntimeError("network error")
        return self.nasdaq100

    def fetch_market_caps(self, tickers):
        self.calls.append("market_caps")
        return {t: self.market_caps[t] for t in tickers if t in self.market_caps}

    def fetch_daily_bars(self, ticker, lookback_days):
        self.calls.append(f"bars:{ticker}")
        return self.bars.get(ticker, [])


def test_refresh_membership_dedupes_overlap_preferring_sp500_category():
    client = FakeMarketDataClient(
        sp500=["A", "B", "C"],
        nasdaq100=["C", "D"],
        market_caps={"A": 100.0, "B": 300.0, "C": 200.0, "D": 50.0},
    )
    cfg = UniverseConfig(top_n_sp500=2, top_n_nasdaq100=2)

    members = refresh_membership(client, cfg)

    by_symbol = {m.symbol: m for m in members}
    assert set(by_symbol) == {"B", "C", "D"}
    assert by_symbol["C"].category == "sp500"
    assert by_symbol["B"].market_cap == 300.0


def test_get_membership_returns_cached_members_without_network_when_fresh(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    save_cache(cache_path, UniverseCache(fetched_at=today, members=[CachedMember("AAPL", "sp500", 3.0e12)]))
    client = FakeMarketDataClient()
    cfg = UniverseConfig(cache_max_age_days=7)

    members = get_membership(client, cache_path, cfg, today, force_refresh=False)

    assert [m.symbol for m in members] == ["AAPL"]
    assert client.calls == []


def test_get_membership_refreshes_and_saves_when_cache_missing(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    client = FakeMarketDataClient(sp500=["A", "B"], nasdaq100=[], market_caps={"A": 100.0, "B": 300.0})
    cfg = UniverseConfig(top_n_sp500=2, top_n_nasdaq100=2)

    members = get_membership(client, cache_path, cfg, today, force_refresh=False)

    assert {m.symbol for m in members} == {"A", "B"}
    reloaded = load_cache(cache_path)
    assert reloaded.fetched_at == today
    assert {m.symbol for m in reloaded.members} == {"A", "B"}


def test_get_membership_force_refresh_ignores_fresh_cache(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    save_cache(cache_path, UniverseCache(fetched_at=today, members=[CachedMember("OLD", "sp500", 1.0)]))
    client = FakeMarketDataClient(sp500=["NEW"], nasdaq100=[], market_caps={"NEW": 500.0})
    cfg = UniverseConfig(top_n_sp500=1, top_n_nasdaq100=1)

    members = get_membership(client, cache_path, cfg, today, force_refresh=True)

    assert [m.symbol for m in members] == ["NEW"]
    assert "sp500" in client.calls


def test_get_membership_falls_back_to_existing_cache_on_fetch_failure(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    stale_date = date(2026, 7, 1)
    today = date(2026, 7, 19)
    save_cache(cache_path, UniverseCache(fetched_at=stale_date, members=[CachedMember("OLD", "sp500", 1.0)]))
    client = FakeMarketDataClient(raise_on_fetch=True)
    cfg = UniverseConfig(cache_max_age_days=7)

    members = get_membership(client, cache_path, cfg, today, force_refresh=False)

    assert [m.symbol for m in members] == ["OLD"]


def test_get_membership_raises_on_fetch_failure_with_no_cache(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    client = FakeMarketDataClient(raise_on_fetch=True)
    cfg = UniverseConfig()

    with pytest.raises(RuntimeError):
        get_membership(client, cache_path, cfg, today, force_refresh=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'refresh_membership'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py`:

```python
def refresh_membership(client: MarketDataClient, cfg: UniverseConfig) -> list[CachedMember]:
    sp500 = client.fetch_sp500_tickers()
    nasdaq = client.fetch_nasdaq100_tickers()
    all_tickers = sorted(set(sp500) | set(nasdaq))
    market_caps = client.fetch_market_caps(all_tickers)

    top_sp500 = rank_top_by_market_cap(sp500, market_caps, cfg.top_n_sp500)
    top_nasdaq = rank_top_by_market_cap(nasdaq, market_caps, cfg.top_n_nasdaq100)

    members: dict[str, CachedMember] = {}
    for ticker in top_sp500:
        members[ticker] = CachedMember(ticker, "sp500", market_caps[ticker])
    for ticker in top_nasdaq:
        if ticker not in members:
            members[ticker] = CachedMember(ticker, "nasdaq100", market_caps[ticker])
    return list(members.values())


def get_membership(
    client: MarketDataClient,
    cache_path: Path,
    cfg: UniverseConfig,
    today: date,
    force_refresh: bool = False,
) -> list[CachedMember]:
    cache = load_cache(cache_path)
    if not force_refresh and not is_cache_stale(cache, today, cfg.cache_max_age_days):
        return cache.members

    try:
        members = refresh_membership(client, cfg)
    except Exception:
        if cache is not None:
            return cache.members
        raise

    save_cache(cache_path, UniverseCache(fetched_at=today, members=members))
    return members
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (26 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add cache-aware membership refresh with dedup and fallback"
```

---

### Task 9: `build_universe` — Tier 2 volatility ranking

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `tests/test_universe.py`

**Interfaces:**
- Consumes: `get_membership` (Task 8); `realized_volatility` (Task 5); `average_true_range_pct` (Task 6); `percentile_ranks` (Task 7); `CachedMember`, `Candidate`, `UniverseConfig` (Task 1).
- Produces: `build_universe(client: MarketDataClient, cache_path: Path, cfg: UniverseConfig, today: date, force_refresh: bool = False) -> list[Candidate]` — sorted descending by `combined_rank`. A symbol with no bars is dropped. `cfg.ranking_mode` selects `"realized_vol"`, `"atr_pct"`, or `"both"` (average of both percentile ranks).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py`:

```python
from robinhood_bot.universe import build_universe


def test_build_universe_ranks_by_realized_vol_when_mode_is_realized_vol(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    bars_low = [Bar(101.0, 99.0, 100.0), Bar(101.0, 99.5, 100.2), Bar(100.8, 99.6, 100.1)]
    bars_high = [Bar(110.0, 90.0, 100.0), Bar(115.0, 85.0, 105.0), Bar(120.0, 80.0, 95.0)]
    client = FakeMarketDataClient(
        sp500=["LOW", "HIGH"], nasdaq100=[],
        market_caps={"LOW": 100.0, "HIGH": 200.0},
        bars={"LOW": bars_low, "HIGH": bars_high},
    )
    cfg = UniverseConfig(
        top_n_sp500=2, top_n_nasdaq100=2, leveraged_funds=[],
        realized_vol_window_days=2, atr_window_days=2, ranking_mode="realized_vol",
    )

    candidates = build_universe(client, cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["HIGH", "LOW"]
    assert candidates[0].combined_rank == 1.0
    assert candidates[1].combined_rank == 0.0


def test_build_universe_drops_symbols_with_no_bars(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    client = FakeMarketDataClient(
        sp500=["A", "B"], nasdaq100=[],
        market_caps={"A": 100.0, "B": 200.0},
        bars={"A": [Bar(101.0, 99.0, 100.0), Bar(102.0, 99.0, 101.0)]},
    )
    cfg = UniverseConfig(top_n_sp500=2, top_n_nasdaq100=2, leveraged_funds=[])

    candidates = build_universe(client, cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["A"]


def test_build_universe_includes_leveraged_funds(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    bars = [Bar(101.0, 99.0, 100.0), Bar(102.0, 99.0, 101.0)]
    client = FakeMarketDataClient(sp500=[], nasdaq100=[], market_caps={}, bars={"TQQQ": bars})
    cfg = UniverseConfig(top_n_sp500=0, top_n_nasdaq100=0, leveraged_funds=["TQQQ"])

    candidates = build_universe(client, cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["TQQQ"]
    assert candidates[0].category == "leveraged"


def test_build_universe_both_mode_averages_percentile_ranks(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    bars_a = [Bar(101.0, 99.0, 100.0), Bar(101.0, 99.5, 100.2), Bar(100.8, 99.6, 100.1)]
    bars_b = [Bar(110.0, 90.0, 100.0), Bar(115.0, 85.0, 105.0), Bar(120.0, 80.0, 95.0)]
    client = FakeMarketDataClient(
        sp500=["A", "B"], nasdaq100=[],
        market_caps={"A": 100.0, "B": 200.0},
        bars={"A": bars_a, "B": bars_b},
    )
    cfg = UniverseConfig(
        top_n_sp500=2, top_n_nasdaq100=2, leveraged_funds=[],
        realized_vol_window_days=2, atr_window_days=2, ranking_mode="both",
    )

    candidates = build_universe(client, cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["B", "A"]
    assert candidates[0].combined_rank == 1.0
    assert candidates[1].combined_rank == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_universe'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/universe.py`:

```python
def build_universe(
    client: MarketDataClient,
    cache_path: Path,
    cfg: UniverseConfig,
    today: date,
    force_refresh: bool = False,
) -> list[Candidate]:
    members = get_membership(client, cache_path, cfg, today, force_refresh)
    leveraged = [CachedMember(symbol, "leveraged", 0.0) for symbol in cfg.leveraged_funds]
    all_members = members + leveraged

    lookback = max(cfg.realized_vol_window_days, cfg.atr_window_days) + 1
    realized_vols: dict[str, float] = {}
    atr_pcts: dict[str, float] = {}

    for member in all_members:
        bars = client.fetch_daily_bars(member.symbol, lookback)
        if not bars:
            continue
        closes = [bar.close for bar in bars]
        realized_vols[member.symbol] = realized_volatility(closes[-(cfg.realized_vol_window_days + 1):])
        atr_pcts[member.symbol] = average_true_range_pct(bars[-(cfg.atr_window_days + 1):])

    vol_ranks = percentile_ranks(realized_vols)
    atr_ranks = percentile_ranks(atr_pcts)

    candidates = []
    for member in all_members:
        if member.symbol not in realized_vols:
            continue
        if cfg.ranking_mode == "realized_vol":
            score = vol_ranks[member.symbol]
        elif cfg.ranking_mode == "atr_pct":
            score = atr_ranks[member.symbol]
        else:
            score = (vol_ranks[member.symbol] + atr_ranks[member.symbol]) / 2
        candidates.append(Candidate(
            symbol=member.symbol,
            category=member.category,
            market_cap=member.market_cap,
            realized_vol=realized_vols[member.symbol],
            atr_pct=atr_pcts[member.symbol],
            combined_rank=score,
        ))

    candidates.sort(key=lambda c: c.combined_rank, reverse=True)
    return candidates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (30 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add build_universe volatility ranking orchestration"
```

---

### Task 10: `LiveMarketDataClient` — the real network client

**Files:**
- Create: `robinhood_bot/universe_client.py`
- Modify: `tests/test_universe.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `Bar` from `robinhood_bot.universe` (Task 1).
- Produces: `clean_ticker_for_yfinance(symbol: str) -> str`; `LiveMarketDataClient` implementing the `MarketDataClient` protocol (`fetch_sp500_tickers`, `fetch_nasdaq100_tickers`, `fetch_market_caps`, `fetch_daily_bars`).

**Note on testing:** `clean_ticker_for_yfinance` is a pure function and is TDD'd normally below. `LiveMarketDataClient`'s methods make real HTTP calls (Wikipedia, Yahoo Finance) and are **not** covered by automated tests — per the design spec, they're verified once by hand after implementation (Step 5 below). Confirmed table structure as of this writing: the S&P 500 constituents table is the first table (index `0`) at `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies` with a `"Symbol"` column (dot notation for share classes, e.g. `"BRK.B"`); the Nasdaq-100 constituents table is the first table (index `0`) at `https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies` with a `"Ticker"` column.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_universe.py — append
from robinhood_bot.universe_client import clean_ticker_for_yfinance


def test_clean_ticker_for_yfinance_converts_dot_to_dash():
    assert clean_ticker_for_yfinance("BRK.B") == "BRK-B"


def test_clean_ticker_for_yfinance_leaves_plain_ticker_unchanged():
    assert clean_ticker_for_yfinance("AAPL") == "AAPL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.universe_client'`

- [ ] **Step 3: Add the new dependency**

Edit `requirements.txt`, changing:

```
yfinance>=0.2.40
pandas>=2.0
pytest>=8.0
```

to:

```
yfinance>=0.2.40
pandas>=2.0
lxml>=5.0
pytest>=8.0
```

Then install it: `D:\aiworkspace\robinhood-bot\.venv\Scripts\pip.exe install lxml>=5.0`

- [ ] **Step 4: Write the implementation**

```python
# robinhood_bot/universe_client.py
from __future__ import annotations

import pandas as pd
import yfinance as yf

from .universe import Bar

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"


def clean_ticker_for_yfinance(symbol: str) -> str:
    return symbol.replace(".", "-")


class LiveMarketDataClient:
    def fetch_sp500_tickers(self) -> list[str]:
        tables = pd.read_html(SP500_WIKI_URL)
        symbols = tables[0]["Symbol"].tolist()
        return [clean_ticker_for_yfinance(s) for s in symbols]

    def fetch_nasdaq100_tickers(self) -> list[str]:
        tables = pd.read_html(NASDAQ100_WIKI_URL)
        tickers = tables[0]["Ticker"].tolist()
        return [clean_ticker_for_yfinance(t) for t in tickers]

    def fetch_market_caps(self, tickers: list[str]) -> dict[str, float]:
        market_caps: dict[str, float] = {}
        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).fast_info
                market_cap = info.get("market_cap") or info.get("marketCap")
            except Exception:
                market_cap = None
            if market_cap:
                market_caps[ticker] = float(market_cap)
        return market_caps

    def fetch_daily_bars(self, ticker: str, lookback_days: int) -> list[Bar]:
        history = yf.Ticker(ticker).history(period=f"{lookback_days + 5}d")
        if history.empty:
            return []
        bars = [
            Bar(high=float(row.High), low=float(row.Low), close=float(row.Close))
            for row in history.itertuples()
        ]
        return bars[-lookback_days:]
```

- [ ] **Step 5: Run the pure-function test to verify it passes**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (32 tests)

- [ ] **Step 6: Manually verify the live client once**

Run this by hand (not part of the automated suite) to confirm the real Wikipedia/yfinance calls work end-to-end:

```bash
D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -c "
from robinhood_bot.universe_client import LiveMarketDataClient
client = LiveMarketDataClient()
sp500 = client.fetch_sp500_tickers()
nasdaq = client.fetch_nasdaq100_tickers()
print('S&P 500 count:', len(sp500), sp500[:5])
print('Nasdaq-100 count:', len(nasdaq), nasdaq[:5])
caps = client.fetch_market_caps(['AAPL', 'MSFT'])
print('Market caps:', caps)
bars = client.fetch_daily_bars('AAPL', 20)
print('AAPL bars fetched:', len(bars))
"
```

Expected: S&P 500 count near 500, Nasdaq-100 count near 100, non-zero market caps for AAPL/MSFT, and roughly 20 bars returned for AAPL. If the table structure has changed since this plan was written, the `"Symbol"`/`"Ticker"` column lookups in Step 4 will need updating to match — note any discrepancy in your report.

- [ ] **Step 7: Commit**

```bash
git add robinhood_bot/universe_client.py tests/test_universe.py requirements.txt
git commit -m "feat: add LiveMarketDataClient for Wikipedia/yfinance data"
```

---

### Task 11: `cli.py universe` subcommand

**Files:**
- Modify: `robinhood_bot/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_universe`, `UniverseConfig`, `Candidate` from `robinhood_bot.universe` (Tasks 1, 9); `LiveMarketDataClient` from `robinhood_bot.universe_client` (Task 10).
- Produces: a new `universe` subcommand on `cli.main`: `universe [--refresh] [--mode realized_vol|atr_pct|both]` → prints `{"candidates": [{"symbol", "category", "market_cap", "realized_vol", "atr_pct", "combined_rank"}, ...]}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (add `from robinhood_bot import universe` to the imports):

```python
from robinhood_bot import universe


def test_cli_universe_command_prints_json(monkeypatch, capsys):
    fake_candidates = [
        universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0),
    ]

    def fake_build_universe(client, cache_path, cfg, today, force_refresh):
        return fake_candidates

    monkeypatch.setattr(cli, "build_universe", fake_build_universe)

    exit_code = cli.main(["universe"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidates"][0]["symbol"] == "AAPL"
    assert output["candidates"][0]["combined_rank"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.cli' has no attribute 'build_universe'`

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

from . import commands
from .risk_engine import RiskConfig
from .universe import UniverseConfig, build_universe
from .universe_client import LiveMarketDataClient

LEDGER_PATH = Path("data/ledger.json")
TRADE_LOG_PATH = Path("data/trade_log.csv")
UNIVERSE_CACHE_PATH = Path("data/universe_cache.json")
STARTING_CASH = 10_000.0


def _parse_prices(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    return json.loads(raw)


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

    args = parser.parse_args(argv)
    today = date.today()
    cfg = RiskConfig()

    if args.command == "state":
        result = commands.cmd_state(LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today)
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

Run: `pytest tests/test_cli.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -v`
Expected: PASS (all tests across every file in `tests/`, roughly 85 total)

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: add universe subcommand to cli.py"
```

---

## What This Plan Does Not Cover

- The daily-cycle SKILL.md and stop-loss-sweep SKILL.md procedures.
- Any Robinhood MCP tool usage.
- `TRADING_MODE` (paper/live) wiring for actual order execution.
- Exact numeric tuning of `top_n_sp500`, `top_n_nasdaq100`, lookback windows, and `cache_max_age_days` — left as config defaults per the design spec.

All of the above are separate, later design/planning efforts.
