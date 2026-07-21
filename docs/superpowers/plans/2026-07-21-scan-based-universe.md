# Scan-Based Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Wikipedia/yfinance-based candidate universe with a Robinhood-scanner-based one, so live/paper universe building has no Yahoo Finance dependency (which cloud sandboxes get blocked on regardless of network policy).

**Architecture:** A saved Robinhood scan (market cap + volume filters, RSI/% Change surfaced as columns) replaces membership scraping and market-cap ranking. `cli.py` gains two network-free subcommands (`universe rank`, `universe finalize`) that do the percentile-rank math and MA-trend/golden-cross attachment from agent-supplied JSON, mirroring the pattern already used by `state`/`risk-check`/`record-fill`. The daily-cycle skill orchestrates: `run_scan` → `universe rank` → per-candidate `get_financials` growth filter → `get_equity_historicals`/`get_equity_fundamentals` for finalists → `universe finalize`.

**Tech Stack:** Python 3, pytest, argparse, Robinhood Agentic Trading MCP (`run_scan`, `get_financials`, `get_equity_historicals`, `get_equity_fundamentals`).

## Global Constraints

- No `yfinance`/Wikipedia network calls anywhere in the live/paper universe-building path (spec Goals).
- Percentile-rank math stays in tested Python, not agent arithmetic (spec Goals).
- `Candidate` output shape must keep `symbol`, `category`, `sector`, `rsi`, `ma_trend_bullish`, `golden_cross_bullish`, `combined_rank` so Steps 3-7 of `robinhood-trading/SKILL.md` are unaffected (spec Goals).
- Backtesting's day-by-day ranking (`backtest_commands.rank_candidates_as_of`) and `LiveHistoricalDataFetcher` are untouched (spec Non-goals).
- No mechanical "recent negative news" check — stays discretionary in Step 6 (spec Non-goals).
- `ma_trend_bullish`/`golden_cross_bullish` keep using `get_equity_historicals` + the existing `is_bullish_ma_trend` — not re-derived from scan EMA columns (spec Non-goals).
- Leveraged funds (`TQQQ`, `UPRO`) bypass the scan and growth filter unconditionally, with a fixed `combined_rank` of `0.5` (spec Architecture step 4).
- On any tool failure, skip/omit rather than fabricate data (spec Error Handling) — established codebase-wide convention, not new to this plan.

---

### Task 1: Rewrite `universe.py`'s ranking core

**Files:**
- Modify: `robinhood_bot/universe.py` (full rewrite)
- Test: `tests/test_universe.py` (full rewrite)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `UniverseConfig` (fields: `leveraged_funds`, `rsi_window_days`, `ma_short_window_days`, `ma_long_window_days`, `golden_cross_short_window_days`, `golden_cross_long_window_days`, `growth_lookback_quarters`, `growth_filter_buffer`, `leveraged_combined_rank`), `Candidate` (fields: `symbol: str`, `category: str`, `market_cap: float`, `pct_change: float`, `combined_rank: float`, `sector: str | None`, `rsi: float`, `ma_trend_bullish: bool | None`, `golden_cross_bullish: bool | None`), `rank_by_scan(scan_rows: list[dict], cfg: UniverseConfig) -> list[dict]`, `finalize_candidates(rows: list[dict], closes_by_symbol: dict[str, list[float]], cfg: UniverseConfig) -> list[Candidate]`, plus the unchanged `relative_strength_index`, `is_bullish_ma_trend`, `percentile_ranks`. Task 3 (`cli.py`) calls `rank_by_scan` and `finalize_candidates` directly.

- [ ] **Step 1: Replace `tests/test_universe.py` with the new test suite**

```python
from datetime import date

import pytest

from robinhood_bot.universe import (
    Candidate,
    UniverseConfig,
    finalize_candidates,
    is_bullish_ma_trend,
    percentile_ranks,
    rank_by_scan,
    relative_strength_index,
)


def test_universe_config_defaults():
    cfg = UniverseConfig()
    assert cfg.leveraged_funds == ["TQQQ", "UPRO"]
    assert cfg.rsi_window_days == 14
    assert cfg.ma_short_window_days == 5
    assert cfg.ma_long_window_days == 20
    assert cfg.golden_cross_short_window_days == 50
    assert cfg.golden_cross_long_window_days == 200
    assert cfg.growth_lookback_quarters == 5
    assert cfg.growth_filter_buffer == 40
    assert cfg.leveraged_combined_rank == 0.5


def test_candidate_fields():
    candidate = Candidate(
        symbol="AAPL", category="scanned", market_cap=3.0e12, pct_change=2.0,
        combined_rank=0.9, sector="Technology", rsi=62.0,
        ma_trend_bullish=True, golden_cross_bullish=True,
    )
    assert candidate.symbol == "AAPL"
    assert candidate.combined_rank == 0.9
    assert candidate.sector == "Technology"


def test_relative_strength_index_insufficient_data_is_neutral():
    assert relative_strength_index([100.0, 101.0, 102.0]) == 50.0
    assert relative_strength_index([]) == 50.0


def test_relative_strength_index_all_gains_is_100():
    closes = [100.0 + i for i in range(15)]
    assert relative_strength_index(closes) == pytest.approx(100.0)


def test_relative_strength_index_all_losses_is_zero():
    closes = [114.0 - i for i in range(15)]
    assert relative_strength_index(closes) == pytest.approx(0.0)


def test_relative_strength_index_mixed_known_value():
    closes = [100.0, 102.0, 101.0, 103.0, 102.0, 104.0, 103.0, 105.0, 104.0, 106.0, 105.0, 107.0, 106.0, 108.0, 107.0]
    assert relative_strength_index(closes) == pytest.approx(66.666666, rel=1e-4)


def test_relative_strength_index_uses_wilder_smoothing_over_full_history():
    closes = [
        103.0, 105.0, 107.0, 110.0, 109.0, 111.0, 113.0, 115.0, 118.0, 120.0,
        123.0, 126.0, 125.5, 127.5, 127.0, 128.5, 130.5, 132.5, 134.5, 136.5,
        138.5, 138.0, 137.5, 139.5, 139.0, 141.0, 144.0, 147.0, 150.0, 149.5,
        151.0, 153.0, 154.5, 154.0, 153.0, 151.0, 149.0, 151.0, 149.0, 147.0,
    ]
    result = relative_strength_index(closes)
    assert result == pytest.approx(64.0932449136437, rel=1e-9)
    assert result != pytest.approx(61.53846153846153, rel=1e-4)


def test_is_bullish_ma_trend_insufficient_data_is_none():
    assert is_bullish_ma_trend([100.0] * 10) is None
    assert is_bullish_ma_trend([]) is None


def test_is_bullish_ma_trend_true_when_short_average_above_long_average():
    closes = [90.0] * 15 + [110.0] * 5
    assert is_bullish_ma_trend(closes) is True


def test_is_bullish_ma_trend_false_when_short_average_at_or_below_long_average():
    closes = [110.0] * 15 + [90.0] * 5
    assert is_bullish_ma_trend(closes) is False


def test_percentile_ranks_empty_input():
    assert percentile_ranks({}) == {}


def test_percentile_ranks_single_entry_is_one():
    assert percentile_ranks({"A": 5.0}) == {"A": 1.0}


def test_percentile_ranks_orders_ascending():
    result = percentile_ranks({"A": 1.0, "B": 3.0, "C": 2.0})
    assert result == {"A": 0.0, "C": 0.5, "B": 1.0}


def test_rank_by_scan_computes_combined_rank_from_pct_change_and_rsi():
    scan_rows = [
        {"symbol": "A", "market_cap": 1.0e11, "pct_change": 1.0, "rsi": 40.0},
        {"symbol": "B", "market_cap": 2.0e11, "pct_change": 5.0, "rsi": 60.0},
        {"symbol": "C", "market_cap": 3.0e11, "pct_change": 3.0, "rsi": 50.0},
    ]

    ranked = rank_by_scan(scan_rows, UniverseConfig())

    assert [r["symbol"] for r in ranked] == ["B", "C", "A"]
    assert ranked[0]["combined_rank"] == 1.0
    assert ranked[1]["combined_rank"] == 0.5
    assert ranked[2]["combined_rank"] == 0.0


def test_rank_by_scan_preserves_other_row_fields():
    scan_rows = [{"symbol": "A", "market_cap": 1.0e11, "pct_change": 1.0, "rsi": 40.0}]

    ranked = rank_by_scan(scan_rows, UniverseConfig())

    assert ranked[0]["market_cap"] == 1.0e11


def test_rank_by_scan_empty_input_returns_empty_list():
    assert rank_by_scan([], UniverseConfig()) == []


def test_finalize_candidates_attaches_ma_trend_when_closes_present():
    rows = [{
        "symbol": "AAPL", "category": "scanned", "market_cap": 3.0e12, "pct_change": 2.0,
        "combined_rank": 0.8, "sector": "Technology", "rsi": 62.0,
    }]
    closes = [90.0] * 15 + [110.0] * 5

    candidates = finalize_candidates(rows, {"AAPL": closes}, UniverseConfig())

    assert candidates[0].symbol == "AAPL"
    assert candidates[0].combined_rank == 0.8
    assert candidates[0].sector == "Technology"
    assert candidates[0].ma_trend_bullish is True


def test_finalize_candidates_attaches_golden_cross_with_sufficient_history():
    rows = [{
        "symbol": "AAPL", "category": "scanned", "market_cap": 3.0e12, "pct_change": 2.0,
        "combined_rank": 0.8, "sector": "Technology", "rsi": 62.0,
    }]
    closes = [100.0 + i * 0.1 for i in range(201)]

    candidates = finalize_candidates(rows, {"AAPL": closes}, UniverseConfig())

    assert candidates[0].golden_cross_bullish is True


def test_finalize_candidates_null_ma_trend_and_golden_cross_when_closes_missing():
    rows = [{
        "symbol": "TQQQ", "category": "leveraged", "market_cap": 0.0, "pct_change": 0.0,
        "combined_rank": 0.5, "sector": None, "rsi": 50.0,
    }]

    candidates = finalize_candidates(rows, {}, UniverseConfig())

    assert candidates[0].ma_trend_bullish is None
    assert candidates[0].golden_cross_bullish is None


def test_finalize_candidates_preserves_input_order():
    rows = [
        {"symbol": "B", "category": "scanned", "market_cap": 1.0, "pct_change": 1.0,
         "combined_rank": 0.9, "sector": "Tech", "rsi": 55.0},
        {"symbol": "A", "category": "scanned", "market_cap": 1.0, "pct_change": 1.0,
         "combined_rank": 0.7, "sector": "Tech", "rsi": 55.0},
    ]

    candidates = finalize_candidates(rows, {}, UniverseConfig())

    assert [c.symbol for c in candidates] == ["B", "A"]
```

- [ ] **Step 2: Run tests to verify they fail on missing names**

Run: `.venv/Scripts/python -m pytest tests/test_universe.py -v`
Expected: FAIL — `ImportError: cannot import name 'rank_by_scan' from 'robinhood_bot.universe'` (and similarly for `finalize_candidates`; `Candidate`/`UniverseConfig` still exist but with the old fields, so some tests will fail on missing/unexpected attributes instead — either failure mode is expected at this point).

- [ ] **Step 3: Replace `robinhood_bot/universe.py` with the new implementation**

```python
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class UniverseConfig:
    leveraged_funds: list[str] = field(default_factory=lambda: ["TQQQ", "UPRO"])
    rsi_window_days: int = 14
    ma_short_window_days: int = 5
    ma_long_window_days: int = 20
    golden_cross_short_window_days: int = 50
    golden_cross_long_window_days: int = 200
    growth_lookback_quarters: int = 5
    growth_filter_buffer: int = 40
    leveraged_combined_rank: float = 0.5


@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    pct_change: float
    combined_rank: float
    sector: str | None = None
    rsi: float = 50.0
    ma_trend_bullish: bool | None = None
    golden_cross_bullish: bool | None = None


def relative_strength_index(closes: list[float], window_days: int = 14) -> float:
    if len(closes) < window_days + 1:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:window_days]) / window_days
    avg_loss = sum(losses[:window_days]) / window_days
    for i in range(window_days, len(changes)):
        avg_gain = (avg_gain * (window_days - 1) + gains[i]) / window_days
        avg_loss = (avg_loss * (window_days - 1) + losses[i]) / window_days
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def is_bullish_ma_trend(closes: list[float], short_window: int = 5, long_window: int = 20) -> bool | None:
    if len(closes) < long_window:
        return None
    short_avg = sum(closes[-short_window:]) / short_window
    long_avg = sum(closes[-long_window:]) / long_window
    return short_avg > long_avg


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda s: values[s])
    n = len(ordered)
    if n == 1:
        return {ordered[0]: 1.0}
    return {symbol: i / (n - 1) for i, symbol in enumerate(ordered)}


def rank_by_scan(scan_rows: list[dict], cfg: UniverseConfig) -> list[dict]:
    pct_changes = {row["symbol"]: row["pct_change"] for row in scan_rows}
    rsis = {row["symbol"]: row["rsi"] for row in scan_rows}
    pct_change_ranks = percentile_ranks(pct_changes)
    rsi_ranks = percentile_ranks(rsis)

    ranked = []
    for row in scan_rows:
        symbol = row["symbol"]
        combined_rank = (pct_change_ranks[symbol] + rsi_ranks[symbol]) / 2
        ranked.append({**row, "combined_rank": combined_rank})

    ranked.sort(key=lambda r: r["combined_rank"], reverse=True)
    return ranked


def finalize_candidates(
    rows: list[dict], closes_by_symbol: dict[str, list[float]], cfg: UniverseConfig,
) -> list[Candidate]:
    candidates = []
    for row in rows:
        closes = closes_by_symbol.get(row["symbol"])
        if closes:
            ma_trend_bullish = is_bullish_ma_trend(closes, cfg.ma_short_window_days, cfg.ma_long_window_days)
            golden_cross_bullish = is_bullish_ma_trend(
                closes, cfg.golden_cross_short_window_days, cfg.golden_cross_long_window_days
            )
        else:
            ma_trend_bullish = None
            golden_cross_bullish = None
        candidates.append(Candidate(
            symbol=row["symbol"],
            category=row["category"],
            market_cap=row["market_cap"],
            pct_change=row["pct_change"],
            combined_rank=row["combined_rank"],
            sector=row.get("sector"),
            rsi=row["rsi"],
            ma_trend_bullish=ma_trend_bullish,
            golden_cross_bullish=golden_cross_bullish,
        ))
    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_universe.py -v`
Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "refactor: replace universe.py's membership/cache system with scan-based ranking"
```

---

### Task 2: Remove `LiveMarketDataClient` from `universe_client.py`

**Files:**
- Modify: `robinhood_bot/universe_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LiveHistoricalDataFetcher` (unchanged, still exported) for Task 5's `_build_price_store` in `cli.py` (backtesting only).

- [ ] **Step 1: Replace `robinhood_bot/universe_client.py` with the trimmed version**

```python
# robinhood_bot/universe_client.py
from __future__ import annotations

from datetime import date, timedelta

import yfinance as yf

from .backtest_data import HistoricalBar


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

- [ ] **Step 2: Run the full test suite to confirm nothing references the deleted class**

Run: `.venv/Scripts/python -m pytest -q`
Expected: FAIL — errors in `tests/test_cli.py` referencing `cli.LiveMarketDataClient` and in `robinhood_bot/cli.py` itself (`ImportError: cannot import name 'LiveMarketDataClient'`). This is expected; Tasks 3-5 fix `cli.py`'s imports and usages. Confirm the failures are only in `cli.py`/`test_cli.py`, not elsewhere (e.g. `tests/test_backtest_data.py`, `tests/test_universe.py` should still pass).

- [ ] **Step 3: Commit**

```bash
git add robinhood_bot/universe_client.py
git commit -m "refactor: delete LiveMarketDataClient (yfinance/Wikipedia universe fetching), unused after the scan-based rewrite"
```

---

### Task 3: Add `cli.py universe rank` / `universe finalize` subcommands

**Files:**
- Modify: `robinhood_bot/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `universe.UniverseConfig`, `universe.rank_by_scan`, `universe.finalize_candidates` (Task 1).
- Produces: `cli.py universe rank --scan-rows-json ...` and `cli.py universe finalize --candidates-json ... --closes-json ...`, replacing the old single `universe [--refresh] [--mode ...]` subcommand. Both print JSON to stdout like every other `cli.py` command.

- [ ] **Step 1: Add failing tests to `tests/test_cli.py`**

Remove the old `test_cli_universe_command_prints_json` test (it calls `cli.main(["universe"])`, which no longer exists) and add these in its place:

```python
def test_cli_universe_rank_command_prints_ranked_json(capsys):
    scan_rows_json = json.dumps([
        {"symbol": "A", "market_cap": 1.0e11, "pct_change": 1.0, "rsi": 40.0},
        {"symbol": "B", "market_cap": 2.0e11, "pct_change": 5.0, "rsi": 60.0},
    ])

    exit_code = cli.main(["universe", "rank", "--scan-rows-json", scan_rows_json])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ranked"][0]["symbol"] == "B"
    assert output["ranked"][0]["combined_rank"] == 1.0
    assert output["ranked"][1]["symbol"] == "A"


def test_cli_universe_finalize_command_prints_candidates_json(capsys):
    candidates_json = json.dumps([{
        "symbol": "AAPL", "category": "scanned", "market_cap": 3.0e12, "pct_change": 2.0,
        "combined_rank": 0.8, "sector": "Technology", "rsi": 62.0,
    }])
    closes = [90.0] * 15 + [110.0] * 5
    closes_json = json.dumps({"AAPL": closes})

    exit_code = cli.main([
        "universe", "finalize", "--candidates-json", candidates_json, "--closes-json", closes_json,
    ])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidates"][0]["symbol"] == "AAPL"
    assert output["candidates"][0]["combined_rank"] == 0.8
    assert output["candidates"][0]["sector"] == "Technology"
    assert output["candidates"][0]["ma_trend_bullish"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -k "universe" -v`
Expected: FAIL — `argparse` error (`invalid choice: 'rank'`) since the `universe` subparser doesn't have `rank`/`finalize` subcommands yet.

- [ ] **Step 3: Replace the `universe` subparser and its dispatch in `robinhood_bot/cli.py`**

In the imports at the top, replace:

```python
from .universe import UniverseConfig, build_universe, is_bullish_ma_trend, relative_strength_index
from .universe_client import LiveHistoricalDataFetcher, LiveMarketDataClient
```

with:

```python
from .universe import UniverseConfig, finalize_candidates, is_bullish_ma_trend, rank_by_scan, relative_strength_index
from .universe_client import LiveHistoricalDataFetcher
```

Replace the `p_universe` block (currently `p_universe = sub.add_parser("universe")` with `--refresh`/`--mode`) with:

```python
    p_universe = sub.add_parser("universe")
    universe_sub = p_universe.add_subparsers(dest="universe_command", required=True)

    p_universe_rank = universe_sub.add_parser("rank")
    p_universe_rank.add_argument("--scan-rows-json", required=True)

    p_universe_finalize = universe_sub.add_parser("finalize")
    p_universe_finalize.add_argument("--candidates-json", required=True)
    p_universe_finalize.add_argument("--closes-json", default=None)
```

Replace the final `else:` branch of `main()` (the one currently calling `build_universe(...)` and building the `result["candidates"]` list) with:

```python
    else:
        universe_cfg = UniverseConfig()
        if args.universe_command == "rank":
            scan_rows = json.loads(args.scan_rows_json)
            ranked = rank_by_scan(scan_rows, universe_cfg)
            result = {"ranked": ranked}
        else:
            candidates_rows = json.loads(args.candidates_json)
            closes_by_symbol = _parse_closes(args.closes_json)
            candidates = finalize_candidates(candidates_rows, closes_by_symbol, universe_cfg)
            result = {
                "candidates": [
                    {
                        "symbol": c.symbol,
                        "category": c.category,
                        "market_cap": c.market_cap,
                        "pct_change": c.pct_change,
                        "combined_rank": c.combined_rank,
                        "sector": c.sector,
                        "rsi": c.rsi,
                        "ma_trend_bullish": c.ma_trend_bullish,
                        "golden_cross_bullish": c.golden_cross_bullish,
                    }
                    for c in candidates
                ]
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -k "universe" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: replace cli.py universe command with rank/finalize subcommands"
```

---

### Task 4: Remove the `yfinance` fallback from `cli.py state`

**Files:**
- Modify: `robinhood_bot/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no behavior change to `cli.py state`'s public contract when `--closes-json` is provided; when omitted, held positions now get neutral `rsi=50.0`/`ma_trend_bullish=None`/`golden_cross_bullish=None` instead of a live `yfinance` fetch.

This closes a gap found while deleting `LiveMarketDataClient`: `cli.py state`'s `else` branch (no `--closes-json` given) currently calls `LiveMarketDataClient().fetch_daily_bars(...)` per held symbol — the exact same Yahoo-blocked call this whole plan exists to remove, just reached from a different command. `robinhood-trading/SKILL.md`'s Step 1 calls `state --prices-json "{}"` with no `--closes-json`, so this path runs live any cycle with open positions.

- [ ] **Step 1: Replace the two `LiveMarketDataClient`-mocking tests in `tests/test_cli.py`**

Delete `test_cli_state_command_fetches_indicators_for_held_positions` and `test_cli_state_command_fetches_golden_cross_for_held_positions` (both monkeypatch `cli.LiveMarketDataClient`, which no longer exists). Replace them with:

```python
def test_cli_state_command_uses_neutral_defaults_for_held_positions_without_closes_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")

    from robinhood_bot import ledger as ledger_module
    from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState

    ledger_module.save_state(
        tmp_path / "ledger.json",
        PortfolioState(cash=5_000.0, active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
        ]),
    )

    exit_code = cli.main(["state", "--prices-json", '{"AAPL": 124.0}'])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["active_positions"][0]["rsi"] == 50.0
    assert output["active_positions"][0]["ma_trend_bullish"] is None
    assert output["active_positions"][0]["golden_cross_bullish"] is None
```

Leave `test_cli_state_command_uses_closes_json_for_indicators_when_provided` as-is (it doesn't reference `LiveMarketDataClient` and should still pass once Step 3 below lands — its `ExplodingClient`/`monkeypatch.setattr(cli, "LiveMarketDataClient", ...)` lines should be deleted since that attribute won't exist, but the test's actual behavior — that supplying `--closes-json` is used for indicators — is unaffected; just remove the now-invalid monkeypatch lines from it).

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -k "held_positions" -v`
Expected: FAIL — `AttributeError` (`cli` module has no attribute `LiveMarketDataClient`) from the still-present monkeypatch line in `test_cli_state_command_uses_closes_json_for_indicators_when_provided`, and the new test fails because the current code still tries to construct `LiveMarketDataClient()`.

- [ ] **Step 3: Replace the `state` command's `else` branch in `robinhood_bot/cli.py`**

Replace this block inside `main()`'s `if args.command == "state":` branch:

```python
        else:
            market_client = LiveMarketDataClient()
            lookback = max(
                universe_cfg.rsi_window_days + 1, universe_cfg.ma_long_window_days,
                universe_cfg.golden_cross_long_window_days,
            ) + 5
            for symbol in held_symbols:
                bars = market_client.fetch_daily_bars(symbol, lookback)
                closes = [bar.close for bar in bars]
                rsi_by_symbol[symbol] = relative_strength_index(closes, universe_cfg.rsi_window_days)
                ma_trend_by_symbol[symbol] = is_bullish_ma_trend(
                    closes, universe_cfg.ma_short_window_days, universe_cfg.ma_long_window_days
                )
                golden_cross_by_symbol[symbol] = is_bullish_ma_trend(
                    closes, universe_cfg.golden_cross_short_window_days, universe_cfg.golden_cross_long_window_days
                )
```

with:

```python
        # No --closes-json provided: leave indicators at neutral defaults
        # rather than fetching live data from this network-free command.
        # (rsi_by_symbol/ma_trend_by_symbol/golden_cross_by_symbol simply
        # stay empty dicts here; cmd_state already treats a missing entry
        # as "unknown" and applies its own neutral defaults downstream.)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: PASS — all of `test_cli.py` green.

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "fix: stop cli.py state from falling back to yfinance when --closes-json is omitted"
```

---

### Task 5: Change `backtest run` to accept an explicit candidate list

**Files:**
- Modify: `robinhood_bot/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `cli.py backtest run` gains a required `--candidates-json` argument (a JSON array of `{"symbol": str, "sector": str | null}`) and no longer calls `build_universe`/`LiveMarketDataClient` internally.

`backtest run` previously called `build_universe(LiveMarketDataClient(), ...)` once to source "today's live universe, applied retroactively" (an already-accepted survivorship-bias simplification per `docs/superpowers/specs/2026-07-19-backtesting-design.md`). Since `build_universe`/`LiveMarketDataClient` no longer exist, the caller now supplies that same kind of snapshot explicitly.

- [ ] **Step 1: Replace `test_cli_backtest_run_command_delegates_to_backtest_commands` in `tests/test_cli.py`**

```python
def test_cli_backtest_run_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    captured = {}

    def fake_cmd_backtest_run(
        run_id, base_dir, starting_cash, start, end, candidate_symbols, candidate_sectors, store, cfg,
        benchmark_symbol,
    ):
        captured["candidate_symbols"] = candidate_symbols
        captured["candidate_sectors"] = candidate_sectors
        return {"run_id": run_id, "trading_days": 0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_run", fake_cmd_backtest_run)

    candidates_json = json.dumps([
        {"symbol": "AAPL", "sector": "Technology"},
        {"symbol": "TQQQ", "sector": None},
    ])
    exit_code = cli.main([
        "backtest", "run", "--run", "run1", "--start", "2026-01-01", "--end", "2026-01-05",
        "--candidates-json", candidates_json,
    ])

    assert exit_code == 0
    assert captured["candidate_symbols"] == ["AAPL", "TQQQ"]
    assert captured["candidate_sectors"] == {"AAPL": "Technology"}
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run1"
```

(This also removes the old test's `monkeypatch.setattr(cli, "build_universe", ...)` line and the `universe.Candidate(...)` fixture — `backtest run` no longer touches either.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -k backtest_run -v`
Expected: FAIL — `argparse` error, `the following arguments are required: --candidates-json` (not yet added), or an `AttributeError` if `build_universe` monkeypatch target no longer resolves.

- [ ] **Step 3: Update the `backtest run` subparser and dispatch in `robinhood_bot/cli.py`**

Add to the `p_bt_run` subparser block:

```python
    p_bt_run.add_argument("--candidates-json", required=True)
```

Replace the `"run"` branch of `_dispatch_backtest`:

```python
    if args.backtest_command == "run":
        store = _build_price_store()
        candidates = json.loads(args.candidates_json)
        candidate_symbols = [c["symbol"] for c in candidates]
        candidate_sectors = {c["symbol"]: c["sector"] for c in candidates if c.get("sector")}
        cfg_overrides = {}
        if args.slots is not None:
            cfg_overrides["max_active_positions"] = args.slots
        if args.weekly_profit_goal is not None:
            cfg_overrides["weekly_profit_goal"] = args.weekly_profit_goal
        run_cfg = RiskConfig(**cfg_overrides) if cfg_overrides else cfg
        return backtest_commands.cmd_backtest_run(
            args.run, BACKTEST_BASE_DIR, args.starting_cash, date.fromisoformat(args.start),
            date.fromisoformat(args.end), candidate_symbols, candidate_sectors, store, run_cfg,
            BENCHMARK_SYMBOL,
        )
```

Delete the now-unused module-level constants (their last two call sites — the `universe` command's old `else` branch and this `backtest run` branch — were removed in Task 3 and this step, respectively):

```python
UNIVERSE_CACHE_PATH = Path("data/universe_cache.json")
SECTOR_CACHE_PATH = Path("data/sector_cache.json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: PASS — all of `test_cli.py` green.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS — all tests across the project green (confirms Tasks 1-5 are fully wired together with no stragglers).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: source backtest run's candidate list from an explicit --candidates-json argument"
```

---

### Task 6: Create the real Robinhood scan

**Files:** none (no repo files change; this task creates a persistent object on the connected Robinhood account via MCP tool calls made directly by whoever executes this task).

**Interfaces:**
- Consumes: `mcp__claude_ai_RobinhoodMCP__create_scan`, `update_scan_filters`, `update_scan_config`, `run_scan` tools (already connected this session).
- Produces: a `scan_id` string, which Task 7 hardcodes into `robinhood-trading/SKILL.md`.

There is no `delete_scan` tool, but `update_scan_filters`/`update_scan_config` can correct an already-created scan — if a filter value is rejected, fix it via those tools on the same `scan_id` rather than creating a second scan.

- [ ] **Step 1: Create the scan with its filters in one call**

Call `create_scan` with:
- `preset`: `"INITIAL"`
- `title`: `"robinhood-bot candidate universe"`
- `filters`:
  ```json
  [
    {"filter_type": "FILTER_TYPE_INSTRUMENT_TYPE", "predicate": "=", "values": ["stock"]},
    {"filter_type": "FILTER_TYPE_MARKET_CAP", "predicate": ">", "values": ["10000000000"]},
    {"filter_type": "FILTER_TYPE_AVERAGE_VOLUME", "predicate": ">", "values": ["1000000"], "length": 10, "interval": "1d"},
    {"filter_type": "FILTER_TYPE_PERCENT_CHANGE_FROM_CLOSE", "predicate": "BETWEEN", "values": ["-100", "100"], "interval": "1d", "plot": "Close"},
    {"filter_type": "FILTER_TYPE_RSI", "predicate": "BETWEEN", "values": ["0", "100"], "length": 14, "interval": "1d"}
  ]
  ```

If `FILTER_TYPE_INSTRUMENT_TYPE`'s `"stock"` value is rejected, the error response should name the valid values — retry that one filter with the corrected value via `update_scan_filters` (resending the complete filter list, since it's replace-not-merge), not by abandoning the scan.

- [ ] **Step 2: Confirm the result columns include what ranking needs**

Call `run_scan` with the new `scan_id`. Confirm the returned rows include, per instrument, a market cap value, a % Change value, and an RSI value (the exact column/key names in the response — read them from this actual call rather than assuming, since the tool description doesn't enumerate them). Note the exact field names for Task 7's SKILL.md instructions.

- [ ] **Step 3: Record the `scan_id`**

Save the `scan_id` string returned by `create_scan` — Task 7 needs it verbatim.

---

### Task 7: Rewrite Step 2 of `robinhood-trading/SKILL.md`

**Files:**
- Modify: `.claude/skills/robinhood-trading/SKILL.md`

**Interfaces:**
- Consumes: the `scan_id` from Task 6; `cli.py universe rank`/`universe finalize` from Task 3.
- Produces: Step 2's output feeds unchanged into Step 3 (build today's research shortlist), which already just reads `combined_rank`-sorted candidates plus held positions — no change needed there.

- [ ] **Step 1: Replace Step 2's content**

Find the current Step 2 section (`## Step 2 — Get the ranked universe` through the paragraph ending "...also needed in Step 7.") and replace it with:

```markdown
## Step 2 — Get the ranked universe

Candidates come from a saved Robinhood scan (`scan_id`: `<SCAN_ID FROM TASK 6>`)
rather than an S&P 500/Nasdaq-100 membership list — a deliberate shift from
"large, well-established, ranked by volatility" to "large, liquid, ranked by
momentum + RSI, gated by revenue growth." See
`docs/superpowers/specs/2026-07-21-scan-based-universe-design.md` for the
full rationale.

1. Call `run_scan` with the scan above. This is real-time data, not cached —
   call it fresh every cycle.
2. `python -m robinhood_bot.cli universe rank --scan-rows-json "<run_scan
   rows, mapped to {symbol, market_cap, pct_change, rsi}>"` — returns the
   full result set sorted descending by `combined_rank` (a percentile-rank
   average of `pct_change` and `rsi`, computed in Python, not by hand).
3. Walk that sorted list top-down. For each candidate, in batches of up to
   20 symbols (the `get_financials` per-call limit), call `get_financials`
   (`period: "quarterly"`, `limit: 5`) and compute YoY revenue growth:
   `(revenue_this_quarter - revenue_same_quarter_last_year) /
   revenue_same_quarter_last_year`. Drop any candidate with negative or flat
   growth. If `get_financials` fails for a candidate, drop it too (never
   assume a candidate passes a check that couldn't be verified) and keep
   walking. Stop once 20 survivors are collected or the list runs out.
4. Append the 2 leveraged funds (`TQQQ`, `UPRO`) unconditionally — they
   never go through the scan or the growth filter. Give each a fixed
   `combined_rank` of `0.5` and `sector: null`.
5. For all ~22 finalists: call `get_equity_historicals` (`interval: "day"`,
   `start_time` ~210 calendar days back, batched up to 10 symbols/call) to
   build a `symbol: [chronological closes]` object — same pattern as
   Step 4 below uses for held positions. Also call `get_equity_fundamentals`
   (batched up to 10/call) for each finalist's `sector` (leveraged funds get
   `sector: null` directly, skip fetching fundamentals for them).
6. `python -m robinhood_bot.cli universe finalize --candidates-json "<22
   finalists: symbol, category ('scanned' or 'leveraged'), market_cap,
   pct_change, combined_rank, sector, rsi>" --closes-json "<historicals from
   step 5>"` — attaches `ma_trend_bullish`/`golden_cross_bullish` per
   candidate (`null` for any symbol whose historicals fetch failed or came
   back with fewer than 200 closes — omit that symbol from the closes
   object passed in, exactly like the held-position rule in Step 4 below).

**If `run_scan` fails or returns zero rows:** skip new BUY consideration for
this entire cycle and say so plainly in the Step 9 summary — there is no
fallback candidate source. Held-position management (Step 6's discretionary
calls, and the separate stop-loss-sweep skill) is unaffected, since neither
depends on the candidate universe.

Each candidate in the final list carries `sector` (needed in Step 7 when
gating a BUY), `rsi` (14-day RSI from the scan), `ma_trend_bullish` (5-day
vs. 20-day moving average), and `golden_cross_bullish` (50-day vs. 200-day)
— all three needed in Step 7, exactly as before.
```

- [ ] **Step 2: Confirm Step 3 still reads correctly**

Read the "Step 3 — Build today's research shortlist" section immediately below and confirm its wording ("From the `candidates` list (sorted by `combined_rank`, descending), take... All candidates whose `category` is `"leveraged"`...") still matches the new Step 2's output — `category` values are now `"scanned"`/`"leveraged"` instead of `"sp500"`/`"nasdaq100"`/`"leveraged"`. Update the phrase "whose `category` is `"sp500"` or `"nasdaq100"`" to "whose `category` is `"scanned"`" if present.

- [ ] **Step 3: Manually verify the new Step 2 end-to-end once, for real**

Actually perform the sequence written in Step 1 above, once, with real data (this is the spec's required manual verification, since none of it is covered by pytest): `run_scan` the Task 6 scan, pipe a handful of its rows into `cli.py universe rank`, pick a few top survivors, run one real `get_financials` growth check and one real `get_equity_historicals`/`get_equity_fundamentals` fetch for them, and confirm `cli.py universe finalize` accepts that data and returns a sensible `Candidate` list. Fix anything that doesn't line up (a field name mismatch between what `run_scan` actually returns and what Step 1's instructions assume, etc.) directly in the Step 1 text before moving on.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md
git commit -m "docs: rewrite Step 2 of the daily-cycle skill for the scan-based universe"
```

---

### Task 8: Update `USAGE.md`

**Files:**
- Modify: `USAGE.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Replace the manual CLI reference's universe section**

Find:

```
# Ranked candidate universe (top S&P 500 + top Nasdaq-100 + leveraged
# funds, by market cap, ranked by volatility). Cached weekly by default.
python -m robinhood_bot.cli universe
python -m robinhood_bot.cli universe --refresh
python -m robinhood_bot.cli universe --mode realized_vol   # or atr_pct, both
```

Replace with:

```
# Ranked candidate universe: large-cap + liquid stocks from a saved
# Robinhood scan, ranked by a blend of % change and RSI, gated by a
# revenue-growth filter. Always real-time -- no cache, no --refresh.
# Both commands are network-free; the caller (the daily-cycle skill,
# manually, or a script) supplies the scan/financials/historicals data.
python -m robinhood_bot.cli universe rank --scan-rows-json '[...]'
python -m robinhood_bot.cli universe finalize --candidates-json '[...]' --closes-json '{...}'
```

- [ ] **Step 2: Update the "Where your data lives" section**

Find:

```
- `data/universe_cache.json` — cached index membership + market caps,
  refreshed weekly.
```

Delete that bullet entirely (there is no longer a universe cache — the scan is always real-time).

- [ ] **Step 3: Update the "Current status" section**

Find:

```
- Core engine, universe ranking, both skills, and backtesting are built
  and tested (`pytest` — currently 233 tests, all local/network-free
  except the live Wikipedia/yfinance-touching classes in
  `universe_client.py`, which are verified manually rather than by
  automated test).
```

Replace with:

```
- Core engine, universe ranking, both skills, and backtesting are built
  and tested (`pytest` — all local/network-free; run `pytest -q` for the
  current count). Universe building sources live data from a Robinhood
  scan (see `docs/superpowers/specs/2026-07-21-scan-based-universe-design.md`)
  instead of the yfinance/Wikipedia scrape the original design used —
  `universe_client.py` now only contains `LiveHistoricalDataFetcher`,
  used solely by backtesting.
```

- [ ] **Step 4: Commit**

```bash
git add USAGE.md
git commit -m "docs: update USAGE.md for the scan-based universe command surface"
```
