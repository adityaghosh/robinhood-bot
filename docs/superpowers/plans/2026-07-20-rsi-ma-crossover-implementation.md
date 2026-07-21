# RSI and Moving-Average Crossover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two deterministic technical indicators (14-day RSI, 5/20-day moving-average trend) gate entries in the risk engine and inform discretionary exits, without touching the existing volatility-based universe ranking.

**Architecture:** Two new pure functions in `universe.py` compute RSI and MA-trend from a list of closes. `evaluate_buy` gains a mechanical overbought/no-uptrend rejection gate. `cmd_state` gains optional `rsi_by_symbol`/`ma_trend_by_symbol` lookup parameters (mirroring the existing `prices` parameter) so it stays free of historical-data access; the two callers that DO have historical-data access (`cli.py`'s live `state` dispatch, and `cmd_backtest_state`) compute these dicts before calling it. The backtest entries loop computes RSI/MA fresh per candidate per day (unlike `sector`, these change daily and can't be precomputed once per run).

**Tech Stack:** Python 3.11+, pytest, existing `robinhood_bot` package conventions.

## Global Constraints

- `RiskConfig.rsi_overbought_threshold` default is `70.0`. `UniverseConfig.rsi_window_days` default is `14`; `ma_short_window_days` default is `5`; `ma_long_window_days` default is `20`. All are code-level defaults — no CLI-tunable flags.
- The entry gate is mechanical and Python-enforced (`evaluate_buy`), exactly like the existing sector-concentration check: reject if `rsi > cfg.rsi_overbought_threshold`, reject if `ma_trend_bullish is False` (note `is False`, not falsy — `None` from insufficient history must bypass the check, never fail closed).
- The exit side is purely discretionary — `rsi`/`ma_trend_bullish` are surfaced as data in `cmd_state`'s position summaries; nothing auto-sells on these signals.
- `Candidate.rsi: float = 50.0` and `Candidate.ma_trend_bullish: bool | None = None` are defaulted fields (added after `sector`), matching the existing backward-compatibility pattern — every existing positional `Candidate(...)` construction in the test suite must keep working unchanged. Same for `Position.rsi: float | None = None` / `Position.ma_trend_bullish: bool | None = None`.
- `evaluate_buy` gains two new **required** parameters, `rsi: float` and `ma_trend_bullish: bool | None`, appended after the existing `sector` parameter — required because it has only two real production call sites (`cmd_risk_check`, `cmd_backtest_run`'s entries loop), matching how `sector` itself was made required there.
- `cmd_risk_check`, `cmd_record_fill`, and their backtest wrappers gain **optional**, defaulted `rsi: float = 50.0` / `ma_trend_bullish: bool | None = None` parameters — optional because sell-path callers never need them, matching the existing `sector` parameter's optionality at this layer.
- Full test suite must stay green after every task: run `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v` before each commit.

---

### Task 1: RSI and MA-trend pure functions in `universe.py`

**Files:**
- Modify: `robinhood_bot/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Produces: `relative_strength_index(closes: list[float], window_days: int = 14) -> float`; `is_bullish_ma_trend(closes: list[float], short_window: int = 5, long_window: int = 20) -> bool | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py`, directly after `test_average_true_range_pct_known_value` (before the `percentile_ranks` import section):

```python
from robinhood_bot.universe import relative_strength_index


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


from robinhood_bot.universe import is_bullish_ma_trend


def test_is_bullish_ma_trend_insufficient_data_is_none():
    assert is_bullish_ma_trend([100.0] * 10) is None
    assert is_bullish_ma_trend([]) is None


def test_is_bullish_ma_trend_true_when_short_average_above_long_average():
    closes = [90.0] * 15 + [110.0] * 5
    assert is_bullish_ma_trend(closes) is True


def test_is_bullish_ma_trend_false_when_short_average_at_or_below_long_average():
    closes = [110.0] * 15 + [90.0] * 5
    assert is_bullish_ma_trend(closes) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: FAIL — `ImportError: cannot import name 'relative_strength_index'` (and similarly for `is_bullish_ma_trend`).

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/universe.py`, add these two functions directly after `average_true_range_pct` (before `percentile_ranks`):

```python
def relative_strength_index(closes: list[float], window_days: int = 14) -> float:
    if len(closes) < window_days + 1:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - window_days, len(closes))]
    gains = [c for c in changes if c > 0]
    losses = [-c for c in changes if c < 0]
    avg_gain = sum(gains) / window_days
    avg_loss = sum(losses) / window_days
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: PASS (all existing + 7 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions (purely additive new functions, nothing else changed).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add RSI and moving-average trend pure functions"
```

---

### Task 2: `Candidate.rsi`/`ma_trend_bullish` + `build_universe` integration

**Files:**
- Modify: `robinhood_bot/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `relative_strength_index`, `is_bullish_ma_trend` (Task 1).
- Produces: `Candidate.rsi: float = 50.0`; `Candidate.ma_trend_bullish: bool | None = None`; `UniverseConfig.rsi_window_days: int = 14`; `UniverseConfig.ma_short_window_days: int = 5`; `UniverseConfig.ma_long_window_days: int = 20`; `build_universe` populates both new `Candidate` fields for every resolved candidate (including leveraged funds — RSI/MA apply regardless of sector exemption).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_universe.py`, directly after `test_build_universe_includes_resolved_sector_on_candidate` (the last test in the file):

```python
def test_build_universe_includes_rsi_and_ma_trend_on_candidate(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    bars = [Bar(101.0, 99.0, 100.0 + i) for i in range(25)]
    client = FakeMarketDataClient(
        sp500=["A"], nasdaq100=[],
        market_caps={"A": 100.0},
        bars={"A": bars},
        sectors={"A": "Healthcare"},
    )
    cfg = UniverseConfig(top_n_sp500=1, top_n_nasdaq100=1, leveraged_funds=[])

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert candidates[0].rsi == pytest.approx(100.0)
    assert candidates[0].ma_trend_bullish is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py::test_build_universe_includes_rsi_and_ma_trend_on_candidate -v`
Expected: FAIL — `AttributeError: 'Candidate' object has no attribute 'rsi'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/universe.py`, change `UniverseConfig` from:

```python
@dataclass
class UniverseConfig:
    top_n_sp500: int = 100
    top_n_nasdaq100: int = 20
    leveraged_funds: list[str] = field(default_factory=lambda: ["TQQQ", "UPRO"])
    realized_vol_window_days: int = 20
    atr_window_days: int = 14
    cache_max_age_days: int = 7
    ranking_mode: str = "both"
```

to:

```python
@dataclass
class UniverseConfig:
    top_n_sp500: int = 100
    top_n_nasdaq100: int = 20
    leveraged_funds: list[str] = field(default_factory=lambda: ["TQQQ", "UPRO"])
    realized_vol_window_days: int = 20
    atr_window_days: int = 14
    rsi_window_days: int = 14
    ma_short_window_days: int = 5
    ma_long_window_days: int = 20
    cache_max_age_days: int = 7
    ranking_mode: str = "both"
```

Change `Candidate` from:

```python
@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    realized_vol: float
    atr_pct: float
    combined_rank: float
    sector: str | None = None
```

to:

```python
@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    realized_vol: float
    atr_pct: float
    combined_rank: float
    sector: str | None = None
    rsi: float = 50.0
    ma_trend_bullish: bool | None = None
```

In `build_universe`, change the bars-fetching loop from:

```python
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
```

to:

```python
    lookback = max(cfg.realized_vol_window_days, cfg.atr_window_days, cfg.rsi_window_days + 1, cfg.ma_long_window_days) + 1
    realized_vols: dict[str, float] = {}
    atr_pcts: dict[str, float] = {}
    rsis: dict[str, float] = {}
    ma_trends: dict[str, bool | None] = {}

    for member in all_members:
        bars = client.fetch_daily_bars(member.symbol, lookback)
        if not bars:
            continue
        closes = [bar.close for bar in bars]
        realized_vols[member.symbol] = realized_volatility(closes[-(cfg.realized_vol_window_days + 1):])
        atr_pcts[member.symbol] = average_true_range_pct(bars[-(cfg.atr_window_days + 1):])
        rsis[member.symbol] = relative_strength_index(closes, cfg.rsi_window_days)
        ma_trends[member.symbol] = is_bullish_ma_trend(closes, cfg.ma_short_window_days, cfg.ma_long_window_days)
```

And change the `Candidate(...)` construction inside the final loop from:

```python
        candidates.append(Candidate(
            symbol=member.symbol,
            category=member.category,
            market_cap=member.market_cap,
            realized_vol=realized_vols[member.symbol],
            atr_pct=atr_pcts[member.symbol],
            combined_rank=score,
            sector=sectors[member.symbol],
        ))
```

to:

```python
        candidates.append(Candidate(
            symbol=member.symbol,
            category=member.category,
            market_cap=member.market_cap,
            realized_vol=realized_vols[member.symbol],
            atr_pct=atr_pcts[member.symbol],
            combined_rank=score,
            sector=sectors[member.symbol],
            rsi=rsis[member.symbol],
            ma_trend_bullish=ma_trends[member.symbol],
        ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: PASS (all existing + 1 new test). Note: the new test uses 25 bars of strictly increasing closes (100 through 124), which comfortably exceeds both the RSI window (15) and MA long window (20), giving `rsi == 100.0` (all gains) and `ma_trend_bullish is True` (recent closes average higher than the full 20-close average) — both hand-verifiable from the same monotonic-increase property.

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions — `rsi`/`ma_trend_bullish` are defaulted fields, and every other existing `bars` fixture in `test_universe.py` is far too short (2-3 bars) to exceed the RSI/MA windows, so they all just get the neutral/unknown defaults without affecting any existing assertion.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: compute RSI and MA trend for every universe candidate"
```

---

### Task 3: `Position.rsi`/`ma_trend_bullish` + ledger persistence

**Files:**
- Modify: `robinhood_bot/portfolio_state.py`
- Modify: `robinhood_bot/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces: `Position.rsi: float | None = None`; `Position.ma_trend_bullish: bool | None = None` (both defaulted, backward-compatible, same pattern as `sector`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py`:

```python
def test_save_and_load_round_trip_preserves_rsi_and_ma_trend(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(
        cash=8_000.0,
        active_positions=[
            Position(
                "AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE,
                rsi=72.5, ma_trend_bullish=True,
            )
        ],
    )
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].rsi == 72.5
    assert loaded.active_positions[0].ma_trend_bullish is True


def test_load_state_defaults_missing_rsi_and_ma_trend_to_none_for_old_ledger_files(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "cash": 5_000.0,
        "active_positions": [{
            "symbol": "AAPL", "qty": 10, "entry_price": 100.0,
            "entry_date": "2026-07-01", "status": "ACTIVE", "underwater_since": None,
            "sector": None,
        }],
        "long_hold_positions": [],
        "month": "", "month_start_equity": 0.0, "week": "", "week_realized_pnl": 0.0,
        "prior_week_realized_pnl": 0.0,
    }))

    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].rsi is None
    assert loaded.active_positions[0].ma_trend_bullish is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v`
Expected: FAIL — `TypeError: Position.__init__() got an unexpected keyword argument 'rsi'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/portfolio_state.py`, change the `Position` dataclass from:

```python
@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: date
    status: PositionStatus
    underwater_since: date | None = None
    sector: str | None = None
```

to:

```python
@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: date
    status: PositionStatus
    underwater_since: date | None = None
    sector: str | None = None
    rsi: float | None = None
    ma_trend_bullish: bool | None = None
```

In `robinhood_bot/ledger.py`, change `_position_to_dict` from:

```python
def _position_to_dict(position: Position) -> dict:
    return {
        "symbol": position.symbol,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "status": position.status.value,
        "underwater_since": (
            position.underwater_since.isoformat() if position.underwater_since else None
        ),
        "sector": position.sector,
    }
```

to:

```python
def _position_to_dict(position: Position) -> dict:
    return {
        "symbol": position.symbol,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "status": position.status.value,
        "underwater_since": (
            position.underwater_since.isoformat() if position.underwater_since else None
        ),
        "sector": position.sector,
        "rsi": position.rsi,
        "ma_trend_bullish": position.ma_trend_bullish,
    }
```

And change `_position_from_dict` from:

```python
def _position_from_dict(data: dict) -> Position:
    return Position(
        symbol=data["symbol"],
        qty=data["qty"],
        entry_price=data["entry_price"],
        entry_date=date.fromisoformat(data["entry_date"]),
        status=PositionStatus(data["status"]),
        underwater_since=(
            date.fromisoformat(data["underwater_since"]) if data["underwater_since"] else None
        ),
        sector=data.get("sector"),
    )
```

to:

```python
def _position_from_dict(data: dict) -> Position:
    return Position(
        symbol=data["symbol"],
        qty=data["qty"],
        entry_price=data["entry_price"],
        entry_date=date.fromisoformat(data["entry_date"]),
        status=PositionStatus(data["status"]),
        underwater_since=(
            date.fromisoformat(data["underwater_since"]) if data["underwater_since"] else None
        ),
        sector=data.get("sector"),
        rsi=data.get("rsi"),
        ma_trend_bullish=data.get("ma_trend_bullish"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_ledger.py tests/test_portfolio_state.py -v`
Expected: PASS (all existing + 2 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions (purely additive defaulted fields).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/portfolio_state.py robinhood_bot/ledger.py tests/test_ledger.py
git commit -m "feat: persist a position's entry-time RSI and MA trend in the ledger"
```

---

### Task 4: `RiskConfig.rsi_overbought_threshold` + `evaluate_buy` entry gate

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Interfaces:**
- Produces: `RiskConfig.rsi_overbought_threshold: float = 70.0`; `evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector, rsi: float, ma_trend_bullish: bool | None) -> BuyDecision` (signature changed — `rsi` and `ma_trend_bullish` are new **required** parameters appended after `sector`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_risk_engine.py`, every existing call to `evaluate_buy` must add `rsi=` and `ma_trend_bullish=` kwargs. Update these 11 lines (shown as old -> new):

```python
# was: decision = evaluate_buy(state, "AAPL", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None)
decision = evaluate_buy(state, "AAPL", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_rejects_when_symbol_already_held`)

```python
# was: decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=8_000.0, cfg=cfg, sector=None)
decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=8_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_rejects_when_circuit_breaker_tripped`)

```python
# was: decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None)
decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_rejects_when_no_active_slots`)

```python
# was: decision = evaluate_buy(state, "MSFT", proposed_value=5_000.0, total_equity=10_000.0, cfg=cfg, sector=None)
decision = evaluate_buy(state, "MSFT", proposed_value=5_000.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_rejects_when_oversized`)

```python
# was: decision = evaluate_buy(state, "MSFT", proposed_value=2_000.0, total_equity=10_000.0, cfg=cfg, sector=None)
decision = evaluate_buy(state, "MSFT", proposed_value=2_000.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_rejects_when_insufficient_cash`)

```python
# was: decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None)
decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_approves_happy_path`)

```python
# was: decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector="Technology")
decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector="Technology", rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_rejects_when_sector_concentration_limit_reached`)

```python
# was: decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials")
decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials", rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_approves_when_different_sector_held`)

```python
# was: decision = evaluate_buy(state, "UPRO", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None)
decision = evaluate_buy(state, "UPRO", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_approves_when_sector_none_bypasses_concentration_check`)

```python
# was: decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials")
decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials", rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_approves_when_bonus_slot_from_prior_week_surplus_allows_it`)

```python
# was: decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Energy")
decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Energy", rsi=50.0, ma_trend_bullish=None)
```
(in `test_evaluate_buy_rejects_when_even_boosted_effective_cap_is_reached`)

Then append these four new tests directly after `test_evaluate_buy_rejects_when_even_boosted_effective_cap_is_reached` (the last existing `evaluate_buy` test):

```python
def test_evaluate_buy_rejects_when_overbought():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=75.0, ma_trend_bullish=True,
    )
    assert decision.approved is False
    assert "overbought" in decision.reason


def test_evaluate_buy_rejects_when_no_confirmed_uptrend():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=False,
    )
    assert decision.approved is False
    assert "uptrend" in decision.reason


def test_evaluate_buy_approves_when_ma_trend_unknown_bypasses_check():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None,
    )
    assert decision.approved is True


def test_evaluate_buy_approves_at_exact_rsi_threshold():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=70.0, ma_trend_bullish=True,
    )
    assert decision.approved is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: FAIL — `TypeError: evaluate_buy() missing 2 required positional arguments: 'rsi' and 'ma_trend_bullish'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/risk_engine.py`, change `RiskConfig` from:

```python
@dataclass
class RiskConfig:
    max_active_positions: int = 5
    max_bonus_active_slots: int = 2
    max_positions_per_sector: int = 1
    stop_loss_pct: float = 0.05
    weekly_profit_goal: float = 500.0
    grace_period_days: int = 5
    max_position_pct: float = 0.20
    min_position_pct: float = 0.05
    long_hold_capital_cap_pct: float = 0.30
    monthly_circuit_breaker_pct: float = 0.10
```

to:

```python
@dataclass
class RiskConfig:
    max_active_positions: int = 5
    max_bonus_active_slots: int = 2
    max_positions_per_sector: int = 1
    stop_loss_pct: float = 0.05
    weekly_profit_goal: float = 500.0
    grace_period_days: int = 5
    max_position_pct: float = 0.20
    min_position_pct: float = 0.05
    long_hold_capital_cap_pct: float = 0.30
    monthly_circuit_breaker_pct: float = 0.10
    rsi_overbought_threshold: float = 70.0
```

Then change `evaluate_buy` from:

```python
def evaluate_buy(
    state: PortfolioState,
    symbol: str,
    proposed_value: float,
    total_equity: float,
    cfg: RiskConfig,
    sector: str | None,
) -> BuyDecision:
    max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)

    if state.is_held(symbol):
        return BuyDecision(False, "symbol already held", max_value)

    if sector is not None:
        sector_count = sum(1 for p in state.active_positions if p.sector == sector)
        if sector_count >= cfg.max_positions_per_sector:
            return BuyDecision(
                False,
                f"sector concentration: already at the {cfg.max_positions_per_sector}-position limit for {sector}",
                max_value,
            )

    if circuit_breaker_tripped(state.month_start_equity, total_equity, cfg):
        return BuyDecision(False, "monthly circuit breaker tripped", max_value)
```

to:

```python
def evaluate_buy(
    state: PortfolioState,
    symbol: str,
    proposed_value: float,
    total_equity: float,
    cfg: RiskConfig,
    sector: str | None,
    rsi: float,
    ma_trend_bullish: bool | None,
) -> BuyDecision:
    max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)

    if state.is_held(symbol):
        return BuyDecision(False, "symbol already held", max_value)

    if sector is not None:
        sector_count = sum(1 for p in state.active_positions if p.sector == sector)
        if sector_count >= cfg.max_positions_per_sector:
            return BuyDecision(
                False,
                f"sector concentration: already at the {cfg.max_positions_per_sector}-position limit for {sector}",
                max_value,
            )

    if rsi > cfg.rsi_overbought_threshold:
        return BuyDecision(
            False,
            f"overbought: RSI {rsi:.1f} exceeds {cfg.rsi_overbought_threshold:.0f}",
            max_value,
        )

    if ma_trend_bullish is False:
        return BuyDecision(False, "no confirmed short-term uptrend (short MA at or below long MA)", max_value)

    if circuit_breaker_tripped(state.month_start_equity, total_equity, cfg):
        return BuyDecision(False, "monthly circuit breaker tripped", max_value)
```

(The rest of `evaluate_buy` — the active-slot, sizing, and cash checks — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: PASS (all existing + 4 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: FAIL only in `robinhood_bot/commands.py` and `robinhood_bot/backtest_commands.py`'s `evaluate_buy` call sites (now missing the two new required arguments) — expected, fixed in Tasks 5 and 6. Confirm `test_risk_engine.py`, `test_universe.py`, `test_ledger.py`, `test_portfolio_state.py` are all green.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: reject an overbought or no-uptrend buy in evaluate_buy"
```

---

### Task 5: `commands.py` — buy-side plumbing and `cmd_state` exit-side surfacing

**Files:**
- Modify: `robinhood_bot/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `evaluate_buy(..., rsi, ma_trend_bullish)` (Task 4, now required); `Position.rsi`/`Position.ma_trend_bullish` (Task 3).
- Produces: `cmd_risk_check(..., rsi: float = 50.0, ma_trend_bullish: bool | None = None) -> dict`; `cmd_record_fill(..., rsi: float = 50.0, ma_trend_bullish: bool | None = None) -> dict`; `cmd_state(..., rsi_by_symbol: dict[str, float] | None = None, ma_trend_by_symbol: dict[str, bool | None] | None = None) -> dict` (all optional, defaulted — keeps every existing call site working unchanged); `_position_summary` output gains `"rsi"`/`"ma_trend_bullish"` keys.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`, directly after `test_cmd_risk_check_buy_approves_happy_path`:

```python
def test_cmd_risk_check_buy_rejects_on_overbought_rsi(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, month_start_equity=10_000.0))
    cfg = RiskConfig(max_position_pct=0.20, rsi_overbought_threshold=70.0)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={}, cfg=cfg, rsi=80.0,
    )

    assert result["approved"] is False
    assert "overbought" in result["reason"]
```

Append directly after `test_cmd_record_fill_buy_persists_sector`:

```python
def test_cmd_record_fill_buy_persists_rsi_and_ma_trend(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle",
        rsi=62.5, ma_trend_bullish=True,
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].rsi == 62.5
    assert reloaded.active_positions[0].ma_trend_bullish is True
```

Append directly after `test_cmd_state_includes_effective_max_active_positions_with_bonus`:

```python
def test_cmd_state_includes_fresh_rsi_and_ma_trend_for_held_positions(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, rsi=50.0)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
        rsi_by_symbol={"AAPL": 81.3}, ma_trend_by_symbol={"AAPL": False},
    )

    assert result["active_positions"][0]["rsi"] == 81.3
    assert result["active_positions"][0]["ma_trend_bullish"] is False


def test_cmd_state_defaults_rsi_and_ma_trend_when_not_supplied(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
    )

    assert result["active_positions"][0]["rsi"] == 50.0
    assert result["active_positions"][0]["ma_trend_bullish"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: FAIL — `TypeError: evaluate_buy() missing 2 required positional arguments` (from `cmd_risk_check`'s buy branch not yet passing them), and `KeyError`/`TypeError` on the new `cmd_state`/`cmd_record_fill` tests.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/commands.py`, change `_position_summary` from:

```python
def _position_summary(position, prices: dict[str, float]) -> dict:
    value, stale = _position_value(position, prices)
    pnl_pct = None if stale else ((value - position.cost_basis) / position.cost_basis)
    return {
        "symbol": position.symbol,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "status": position.status.value,
        "current_value": value,
        "unrealized_pnl_pct": pnl_pct,
        "stale_price": stale,
    }
```

to:

```python
def _position_summary(
    position, prices: dict[str, float], rsi_by_symbol: dict[str, float], ma_trend_by_symbol: dict[str, bool | None],
) -> dict:
    value, stale = _position_value(position, prices)
    pnl_pct = None if stale else ((value - position.cost_basis) / position.cost_basis)
    return {
        "symbol": position.symbol,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "status": position.status.value,
        "current_value": value,
        "unrealized_pnl_pct": pnl_pct,
        "stale_price": stale,
        "rsi": rsi_by_symbol.get(position.symbol, 50.0),
        "ma_trend_bullish": ma_trend_by_symbol.get(position.symbol),
    }
```

Change `cmd_state`'s signature and its two `_position_summary` call sites from:

```python
def cmd_state(
    ledger_path: Path,
    starting_cash: float,
    prices: dict[str, float],
    today: date,
    trading_mode: str,
    cfg: RiskConfig,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    active_out = [_position_summary(p, prices) for p in state.active_positions]
    long_hold_out = [_position_summary(p, prices) for p in state.long_hold_positions]
```

to:

```python
def cmd_state(
    ledger_path: Path,
    starting_cash: float,
    prices: dict[str, float],
    today: date,
    trading_mode: str,
    cfg: RiskConfig,
    rsi_by_symbol: dict[str, float] | None = None,
    ma_trend_by_symbol: dict[str, bool | None] | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    rsi_by_symbol = rsi_by_symbol or {}
    ma_trend_by_symbol = ma_trend_by_symbol or {}

    active_out = [_position_summary(p, prices, rsi_by_symbol, ma_trend_by_symbol) for p in state.active_positions]
    long_hold_out = [_position_summary(p, prices, rsi_by_symbol, ma_trend_by_symbol) for p in state.long_hold_positions]
```

Change `cmd_risk_check` from:

```python
def cmd_risk_check(
    ledger_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    proposed_value: float,
    prices: dict[str, float],
    cfg: RiskConfig,
    sector: str | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    positions_value = sum(
        prices.get(p.symbol, p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    total_equity = state.cash + positions_value

    if action == "buy":
        decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector)
        return {
            "approved": decision.approved,
            "reason": decision.reason,
            "max_position_value": decision.max_position_value,
        }
```

to:

```python
def cmd_risk_check(
    ledger_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    proposed_value: float,
    prices: dict[str, float],
    cfg: RiskConfig,
    sector: str | None = None,
    rsi: float = 50.0,
    ma_trend_bullish: bool | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    positions_value = sum(
        prices.get(p.symbol, p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    total_equity = state.cash + positions_value

    if action == "buy":
        decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector, rsi, ma_trend_bullish)
        return {
            "approved": decision.approved,
            "reason": decision.reason,
            "max_position_value": decision.max_position_value,
        }
```

Change `cmd_record_fill`'s signature and buy-branch `Position(...)` construction from:

```python
def cmd_record_fill(
    ledger_path: Path,
    trade_log_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    qty: float,
    price: float,
    today: date,
    reason: str,
    sector: str | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    roll_week_if_needed(state, today)

    if action == "buy":
        if state.is_held(symbol):
            raise ValueError(f"{symbol} already held")
        cost = qty * price
        if cost > state.cash:
            raise ValueError("insufficient cash for fill")
        state.cash -= cost
        state.active_positions.append(
            Position(
                symbol=symbol,
                qty=qty,
                entry_price=price,
                entry_date=today,
                status=PositionStatus.ACTIVE,
                sector=sector,
            )
        )
```

to:

```python
def cmd_record_fill(
    ledger_path: Path,
    trade_log_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    qty: float,
    price: float,
    today: date,
    reason: str,
    sector: str | None = None,
    rsi: float = 50.0,
    ma_trend_bullish: bool | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    roll_week_if_needed(state, today)

    if action == "buy":
        if state.is_held(symbol):
            raise ValueError(f"{symbol} already held")
        cost = qty * price
        if cost > state.cash:
            raise ValueError("insufficient cash for fill")
        state.cash -= cost
        state.active_positions.append(
            Position(
                symbol=symbol,
                qty=qty,
                entry_price=price,
                entry_date=today,
                status=PositionStatus.ACTIVE,
                sector=sector,
                rsi=rsi,
                ma_trend_bullish=ma_trend_bullish,
            )
        )
```

(The rest of both functions — the sell branches — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: PASS (all existing + 4 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: FAIL only in `robinhood_bot/backtest_commands.py`'s `evaluate_buy` call site inside `cmd_backtest_run` (still missing the two new required arguments — Task 6, not yours). Confirm `test_commands.py` and everything from Tasks 1-4 stay green.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: thread RSI/MA trend through cmd_risk_check, cmd_record_fill, and cmd_state"
```

---

### Task 6: `backtest_commands.py` — pass-through, entries-loop gate, and `cmd_backtest_state`

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Test: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `commands.cmd_risk_check(..., rsi, ma_trend_bullish)`, `commands.cmd_record_fill(..., rsi, ma_trend_bullish)`, `commands.cmd_state(..., rsi_by_symbol, ma_trend_by_symbol)` (Task 5); `evaluate_buy(..., rsi, ma_trend_bullish)` (Task 4); `relative_strength_index`, `is_bullish_ma_trend` (Task 1).
- Produces: `cmd_backtest_risk_check(..., rsi: float = 50.0, ma_trend_bullish: bool | None = None) -> dict`; `cmd_backtest_record_fill(..., rsi: float = 50.0, ma_trend_bullish: bool | None = None) -> dict`; `cmd_backtest_state(..., store: HistoricalPriceStore, rsi_window_days: int = 14, ma_short_window_days: int = 5, ma_long_window_days: int = 20) -> dict` (signature changed — `store` is a new **required** parameter); `cmd_backtest_run(..., rsi_window_days: int = 14, ma_short_window_days: int = 5, ma_long_window_days: int = 20) -> dict` (three new optional parameters; no change to required parameters) — the entries loop computes RSI/MA trend fresh per candidate per day and passes them into `evaluate_buy`/`cmd_record_fill`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_backtest_commands.py`, update the one existing `cmd_backtest_state` test. Change `test_cmd_backtest_state_reads_isolated_ledger` from:

```python
def test_cmd_backtest_state_reads_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=5_000.0))

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5), cfg=RiskConfig(),
    )

    assert result["cash"] == 5_000.0
    assert result["trading_mode"] == "backtest"
```

to:

```python
def test_cmd_backtest_state_reads_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=5_000.0))

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return []

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5), cfg=RiskConfig(), store=store,
    )

    assert result["cash"] == 5_000.0
    assert result["trading_mode"] == "backtest"
```

(This test holds no positions, so the `FakeFetcher` is never actually called — `cmd_backtest_state` will have nothing to compute indicators for.)

Append a new test directly after it, proving `cmd_backtest_state` actually computes fresh indicators for a held position:

```python
def test_cmd_backtest_state_includes_fresh_rsi_for_held_position(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 1, 1), PositionStatus.ACTIVE)],
    ))

    bars = [HistoricalBar(date(2026, 1, 1) + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(25)]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 124.0}, asof=date(2026, 1, 25),
        cfg=RiskConfig(), store=store,
    )

    assert result["active_positions"][0]["rsi"] == pytest.approx(100.0)
    assert result["active_positions"][0]["ma_trend_bullish"] is True
```

Note: `import timedelta` from `datetime` at the top of the file if not already present (check — `from datetime import date` is the current import; change it to `from datetime import date, timedelta`).

Now update the six existing `cmd_backtest_run` calls — these need NO changes, since the new `rsi_window_days`/`ma_short_window_days`/`ma_long_window_days` parameters are optional and defaulted, and `evaluate_buy`'s new required parameters are computed INSIDE `cmd_backtest_run` itself (not passed in from the caller) — the existing bars fixtures in these tests are all far too short to exceed the RSI/MA windows, so every entries-loop buy in them will see `rsi=50.0` (neutral, never overbought) and `ma_trend_bullish=None` (insufficient data, bypasses the check), meaning none of their existing assertions change.

Append one new integration test directly after `test_cmd_backtest_run_skips_same_sector_candidate_for_next_ranked` (before `test_cmd_backtest_run_fills_bonus_slot_from_prior_week_surplus`):

```python
def test_cmd_backtest_run_rejects_overbought_candidate_for_next_ranked(tmp_path):
    # AAPL2 ranks first (its bars show a monotonic 25-day rise, giving both
    # the highest volatility/ATR score AND an RSI of 100 -- deeply overbought)
    # but must be REJECTED by the new RSI gate; JPM ranks second (flat/mild
    # bars, neutral RSI) and should be the one actually bought instead. If
    # the RSI gate weren't wired into this loop, AAPL2 (the top-ranked
    # candidate) would be bought instead.
    aapl2_bars = [
        HistoricalBar(date(2025, 12, 15) + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i)
        for i in range(37)
    ]
    jpm_bars = [
        HistoricalBar(date(2025, 12, 15) + timedelta(days=i), 150.0, 150.5, 149.5, 150.0 + (0.1 if i % 2 == 0 else -0.1))
        for i in range(37)
    ]
    bars = {
        "SPY": [HistoricalBar(date(2026, 1, 20), 400.0, 401.0, 399.0, 400.0)],
        "AAPL2": aapl2_bars,
        "JPM": jpm_bars,
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars.get(symbol, []) if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.5, weekly_profit_goal=100_000.0,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
        rsi_overbought_threshold=70.0,
    )

    backtest_commands.cmd_backtest_run(
        "run_rsi", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 20), end=date(2026, 1, 20),
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={}, store=store, cfg=cfg,
        vol_window_days=2, atr_window_days=2,
    )

    paths = backtest_commands.resolve_run_paths("run_rsi", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    assert [p.symbol for p in final_state.active_positions] == ["JPM"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: FAIL — `TypeError: cmd_backtest_state() missing 1 required positional argument: 'store'`, and `TypeError: evaluate_buy() missing 2 required positional arguments` from the entries loop.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/backtest_commands.py`, update the import lines from:

```python
from datetime import date, timedelta
```

(already present — no change needed there) and from:

```python
from .risk_engine import (
    ExitAction, RiskConfig, bonus_active_slots, evaluate_buy, evaluate_position,
    evaluate_profit_exits, max_new_position_value,
)
from .universe import average_true_range_pct, percentile_ranks, realized_volatility
```

to:

```python
from .risk_engine import (
    ExitAction, RiskConfig, bonus_active_slots, evaluate_buy, evaluate_position,
    evaluate_profit_exits, max_new_position_value,
)
from .universe import (
    average_true_range_pct, is_bullish_ma_trend, percentile_ranks, realized_volatility,
    relative_strength_index,
)
```

Change `cmd_backtest_risk_check` from:

```python
def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig, sector: str | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg, sector)
```

to:

```python
def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig, sector: str | None = None,
    rsi: float = 50.0, ma_trend_bullish: bool | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(
        paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg, sector, rsi, ma_trend_bullish,
    )
```

Change `cmd_backtest_record_fill` from:

```python
def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str, sector: str | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason, sector,
    )
```

to:

```python
def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str, sector: str | None = None,
    rsi: float = 50.0, ma_trend_bullish: bool | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason, sector,
        rsi, ma_trend_bullish,
    )
```

Change `cmd_backtest_state` from:

```python
def cmd_backtest_state(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
    cfg: RiskConfig,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_state(paths.ledger, starting_cash, prices, asof, trading_mode="backtest", cfg=cfg)
```

to:

```python
def cmd_backtest_state(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
    cfg: RiskConfig, store: HistoricalPriceStore,
    rsi_window_days: int = 14, ma_short_window_days: int = 5, ma_long_window_days: int = 20,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    state = ledger.load_state(paths.ledger, starting_cash)
    held_symbols = {p.symbol for p in state.active_positions + state.long_hold_positions}

    lookback = max(rsi_window_days + 1, ma_long_window_days)
    rsi_by_symbol: dict[str, float] = {}
    ma_trend_by_symbol: dict[str, bool | None] = {}
    for symbol in held_symbols:
        closes = store.get_closes_window(symbol, asof, lookback)
        rsi_by_symbol[symbol] = relative_strength_index(closes, rsi_window_days)
        ma_trend_by_symbol[symbol] = is_bullish_ma_trend(closes, ma_short_window_days, ma_long_window_days)

    return commands.cmd_state(
        paths.ledger, starting_cash, prices, asof, trading_mode="backtest", cfg=cfg,
        rsi_by_symbol=rsi_by_symbol, ma_trend_by_symbol=ma_trend_by_symbol,
    )
```

Change `cmd_backtest_run`'s signature from:

```python
def cmd_backtest_run(
    run_id: str,
    base_dir: Path,
    starting_cash: float,
    start: date,
    end: date,
    candidate_symbols: list[str],
    candidate_sectors: dict[str, str],
    store: HistoricalPriceStore,
    cfg: RiskConfig,
    benchmark_symbol: str = "SPY",
    vol_window_days: int = 20,
    atr_window_days: int = 14,
) -> dict:
```

to:

```python
def cmd_backtest_run(
    run_id: str,
    base_dir: Path,
    starting_cash: float,
    start: date,
    end: date,
    candidate_symbols: list[str],
    candidate_sectors: dict[str, str],
    store: HistoricalPriceStore,
    cfg: RiskConfig,
    benchmark_symbol: str = "SPY",
    vol_window_days: int = 20,
    atr_window_days: int = 14,
    rsi_window_days: int = 14,
    ma_short_window_days: int = 5,
    ma_long_window_days: int = 20,
) -> dict:
```

Then, within the same function, change the "3. Entries" block's per-candidate section from:

```python
                cash, positions_value = _total_equity(state, store, today)
                total_equity = cash + positions_value
                max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)
                proposed_value = min(max_value, state.cash)
                sector = candidate_sectors.get(symbol)
                decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector)
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry", sector,
                )
```

to:

```python
                cash, positions_value = _total_equity(state, store, today)
                total_equity = cash + positions_value
                max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)
                proposed_value = min(max_value, state.cash)
                sector = candidate_sectors.get(symbol)
                # RSI/MA trend change daily (unlike sector, which is a
                # permanent fact about a symbol) so they can't be
                # precomputed once per run -- fetch fresh here, immediately
                # before the buy decision, mirroring how `price` itself is
                # fetched fresh per candidate per day just above.
                indicator_lookback = max(rsi_window_days + 1, ma_long_window_days)
                closes = store.get_closes_window(symbol, today, indicator_lookback)
                rsi = relative_strength_index(closes, rsi_window_days)
                ma_trend_bullish = is_bullish_ma_trend(closes, ma_short_window_days, ma_long_window_days)
                decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector, rsi, ma_trend_bullish)
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry", sector, rsi, ma_trend_bullish,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (all existing + 2 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: FAIL only in `tests/test_cli.py` (2 tests still call `cmd_backtest_state`/`cmd_backtest_run` through the CLI dispatch, which hasn't been updated yet — Task 7, not yours). Confirm `test_backtest_commands.py` and everything from Tasks 1-5 stay green.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: wire RSI/MA trend into the backtest entries loop and cmd_backtest_state"
```

---

### Task 7: `cli.py` — `--rsi`/`--ma-bullish` flags, `universe` output, live `state`/backtest `state`/`run` wiring

**Files:**
- Modify: `robinhood_bot/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_universe` output with `rsi`/`ma_trend_bullish` (Task 2); `cmd_risk_check(..., rsi, ma_trend_bullish)`, `cmd_record_fill(..., rsi, ma_trend_bullish)`, `cmd_state(..., rsi_by_symbol, ma_trend_by_symbol)` (Task 5); `cmd_backtest_risk_check(..., rsi, ma_trend_bullish)`, `cmd_backtest_record_fill(..., rsi, ma_trend_bullish)`, `cmd_backtest_state(..., store)` (Task 6); `relative_strength_index`, `is_bullish_ma_trend` (Task 1).
- Produces: `--rsi` (float, default `50.0`) and `--ma-bullish` (tri-state boolean flag, default `None`) CLI arguments on live and backtest `risk-check`/`record-fill`; `universe` command output includes `rsi`/`ma_trend_bullish` per candidate; live `state` dispatch fetches recent bars for every held position and computes fresh indicators before calling `cmd_state`; backtest `state` dispatch passes a price store to `cmd_backtest_state`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, replace `test_cli_universe_command_prints_json` with:

```python
def test_cli_universe_command_prints_json(monkeypatch, capsys):
    fake_candidates = [
        universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0, sector="Technology", rsi=62.0, ma_trend_bullish=True),
    ]

    def fake_build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh):
        return fake_candidates

    monkeypatch.setattr(cli, "build_universe", fake_build_universe)

    exit_code = cli.main(["universe"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidates"][0]["symbol"] == "AAPL"
    assert output["candidates"][0]["combined_rank"] == 1.0
    assert output["candidates"][0]["sector"] == "Technology"
    assert output["candidates"][0]["rsi"] == 62.0
    assert output["candidates"][0]["ma_trend_bullish"] is True
```

Replace `test_cli_backtest_state_command_prints_json` with:

```python
def test_cli_backtest_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return []

    monkeypatch.setattr(cli, "LiveHistoricalDataFetcher", FakeFetcher)

    exit_code = cli.main(["backtest", "state", "--run", "run1", "--asof", "2026-01-05"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_mode"] == "backtest"
    assert output["cash"] == cli.STARTING_CASH
```

Replace `test_cli_backtest_run_command_delegates_to_backtest_commands` with:

```python
def test_cli_backtest_run_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    fake_candidates = [universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0, sector="Technology")]
    monkeypatch.setattr(
        cli, "build_universe", lambda client, cache_path, sector_cache_path, cfg, today: fake_candidates
    )

    captured = {}

    def fake_cmd_backtest_run(
        run_id, base_dir, starting_cash, start, end, candidate_symbols, candidate_sectors, store, cfg,
        benchmark_symbol,
    ):
        captured["candidate_symbols"] = candidate_symbols
        captured["candidate_sectors"] = candidate_sectors
        return {"run_id": run_id, "trading_days": 0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_run", fake_cmd_backtest_run)

    exit_code = cli.main(["backtest", "run", "--run", "run1", "--start", "2026-01-01", "--end", "2026-01-05"])

    assert exit_code == 0
    assert captured["candidate_symbols"] == ["AAPL"]
    assert captured["candidate_sectors"] == {"AAPL": "Technology"}
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run1"
```

(This test doesn't change — `cmd_backtest_run`'s new RSI/MA parameters are optional and computed internally, not passed in from `cli.py`, so `fake_cmd_backtest_run`'s signature is unaffected. Included here to confirm no update is needed.)

Append four new tests at the end of the file:

```python
def test_cli_risk_check_buy_passes_rsi_and_ma_bullish_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None,
    ):
        captured["rsi"] = rsi
        captured["ma_trend_bullish"] = ma_trend_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main([
        "risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}",
        "--rsi", "62.5", "--ma-bullish",
    ])

    assert exit_code == 0
    assert captured["rsi"] == 62.5
    assert captured["ma_trend_bullish"] is True


def test_cli_risk_check_buy_defaults_ma_bullish_to_none_when_omitted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None,
    ):
        captured["ma_trend_bullish"] = ma_trend_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main(["risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}"])

    assert exit_code == 0
    assert captured["ma_trend_bullish"] is None


def test_cli_backtest_risk_check_passes_rsi_and_ma_bullish_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    captured = {}

    def fake_cmd_backtest_risk_check(
        run_id, base_dir, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None,
    ):
        captured["rsi"] = rsi
        captured["ma_trend_bullish"] = ma_trend_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_risk_check", fake_cmd_backtest_risk_check)

    exit_code = cli.main([
        "backtest", "risk-check", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--value", "500", "--prices-json", "{}", "--rsi", "45.0", "--no-ma-bullish",
    ])

    assert exit_code == 0
    assert captured["rsi"] == 45.0
    assert captured["ma_trend_bullish"] is False


def test_cli_state_command_fetches_indicators_for_held_positions(tmp_path, monkeypatch, capsys):
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

    class FakeClient:
        def fetch_daily_bars(self, ticker, lookback_days):
            from robinhood_bot.universe import Bar
            return [Bar(101.0 + i, 99.0 + i, 100.0 + i) for i in range(25)]

    monkeypatch.setattr(cli, "LiveMarketDataClient", FakeClient)

    exit_code = cli.main(["state", "--prices-json", '{"AAPL": 124.0}'])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["active_positions"][0]["rsi"] == pytest.approx(100.0)
    assert output["active_positions"][0]["ma_trend_bullish"] is True
```

Note: `test_cli_state_command_fetches_indicators_for_held_positions` needs `import pytest` at the top of `test_cli.py` — check whether it's already imported (it currently isn't; add `import pytest` alongside the existing `import json` / `from datetime import date` imports).

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: FAIL — assorted `TypeError`/`KeyError`/argparse `SystemExit` (unrecognized `--rsi`/`--ma-bullish` flags; `cmd_backtest_state` missing `store`; `universe`/`state` output missing the new fields).

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/cli.py`, update the import line from:

```python
from .universe import UniverseConfig, build_universe
```

to:

```python
from .universe import UniverseConfig, build_universe, is_bullish_ma_trend, relative_strength_index
```

In `_dispatch_backtest`, change the `"state"` case from:

```python
    if args.backtest_command == "state":
        return backtest_commands.cmd_backtest_state(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof), cfg,
        )
```

to:

```python
    if args.backtest_command == "state":
        return backtest_commands.cmd_backtest_state(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof), cfg, _build_price_store(),
        )
```

Change the `"risk-check"` and `"record-fill"` backtest cases from:

```python
    if args.backtest_command == "risk-check":
        return backtest_commands.cmd_backtest_risk_check(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
        )
    if args.backtest_command == "record-fill":
        return backtest_commands.cmd_backtest_record_fill(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, date.fromisoformat(args.asof), args.reason, sector=args.sector,
        )
```

to:

```python
    if args.backtest_command == "risk-check":
        return backtest_commands.cmd_backtest_risk_check(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish,
        )
    if args.backtest_command == "record-fill":
        return backtest_commands.cmd_backtest_record_fill(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, date.fromisoformat(args.asof), args.reason, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish,
        )
```

In `main()`, add `--rsi`/`--ma-bullish` to the live `risk-check` and `record-fill` parsers — change:

```python
    p_risk = sub.add_parser("risk-check")
    p_risk.add_argument("action", choices=["buy", "sell"])
    p_risk.add_argument("symbol")
    p_risk.add_argument("--value", type=float, default=0.0)
    p_risk.add_argument("--prices-json", default=None)
    p_risk.add_argument("--sector", default=None)

    p_fill = sub.add_parser("record-fill")
    p_fill.add_argument("action", choices=["buy", "sell"])
    p_fill.add_argument("symbol")
    p_fill.add_argument("--qty", type=float, required=True)
    p_fill.add_argument("--price", type=float, required=True)
    p_fill.add_argument("--reason", default="")
    p_fill.add_argument("--sector", default=None)
```

to:

```python
    p_risk = sub.add_parser("risk-check")
    p_risk.add_argument("action", choices=["buy", "sell"])
    p_risk.add_argument("symbol")
    p_risk.add_argument("--value", type=float, default=0.0)
    p_risk.add_argument("--prices-json", default=None)
    p_risk.add_argument("--sector", default=None)
    p_risk.add_argument("--rsi", type=float, default=50.0)
    p_risk.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)

    p_fill = sub.add_parser("record-fill")
    p_fill.add_argument("action", choices=["buy", "sell"])
    p_fill.add_argument("symbol")
    p_fill.add_argument("--qty", type=float, required=True)
    p_fill.add_argument("--price", type=float, required=True)
    p_fill.add_argument("--reason", default="")
    p_fill.add_argument("--sector", default=None)
    p_fill.add_argument("--rsi", type=float, default=50.0)
    p_fill.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
```

`record-fill` gets the same two flags as `risk-check` (matching how `--sector` already appears on both) — the trading skill passes the same `--rsi`/`--ma-bullish` values to `record-fill` that it already passed to the preceding `risk-check` call.

Add the same two flags to the backtest `risk-check` and `record-fill` sub-parsers — change:

```python
    p_bt_risk = backtest_sub.add_parser("risk-check")
    p_bt_risk.add_argument("action", choices=["buy", "sell"])
    p_bt_risk.add_argument("symbol")
    p_bt_risk.add_argument("--run", required=True)
    p_bt_risk.add_argument("--asof", required=True)
    p_bt_risk.add_argument("--value", type=float, default=0.0)
    p_bt_risk.add_argument("--prices-json", default=None)
    p_bt_risk.add_argument("--sector", default=None)

    p_bt_fill = backtest_sub.add_parser("record-fill")
    p_bt_fill.add_argument("action", choices=["buy", "sell"])
    p_bt_fill.add_argument("symbol")
    p_bt_fill.add_argument("--run", required=True)
    p_bt_fill.add_argument("--asof", required=True)
    p_bt_fill.add_argument("--qty", type=float, required=True)
    p_bt_fill.add_argument("--price", type=float, required=True)
    p_bt_fill.add_argument("--reason", default="")
    p_bt_fill.add_argument("--sector", default=None)
```

to:

```python
    p_bt_risk = backtest_sub.add_parser("risk-check")
    p_bt_risk.add_argument("action", choices=["buy", "sell"])
    p_bt_risk.add_argument("symbol")
    p_bt_risk.add_argument("--run", required=True)
    p_bt_risk.add_argument("--asof", required=True)
    p_bt_risk.add_argument("--value", type=float, default=0.0)
    p_bt_risk.add_argument("--prices-json", default=None)
    p_bt_risk.add_argument("--sector", default=None)
    p_bt_risk.add_argument("--rsi", type=float, default=50.0)
    p_bt_risk.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)

    p_bt_fill = backtest_sub.add_parser("record-fill")
    p_bt_fill.add_argument("action", choices=["buy", "sell"])
    p_bt_fill.add_argument("symbol")
    p_bt_fill.add_argument("--run", required=True)
    p_bt_fill.add_argument("--asof", required=True)
    p_bt_fill.add_argument("--qty", type=float, required=True)
    p_bt_fill.add_argument("--price", type=float, required=True)
    p_bt_fill.add_argument("--reason", default="")
    p_bt_fill.add_argument("--sector", default=None)
    p_bt_fill.add_argument("--rsi", type=float, default=50.0)
    p_bt_fill.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
```

Change the live `risk-check`/`record-fill` dispatch in `main()` from:

```python
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason, sector=args.sector,
        )
```

to:

```python
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish,
        )
```

Change the live `state` dispatch from:

```python
    if args.command == "state":
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE, cfg
        )
```

to:

```python
    if args.command == "state":
        universe_cfg = UniverseConfig()
        held_state = ledger.load_state(LEDGER_PATH, STARTING_CASH)
        held_symbols = {p.symbol for p in held_state.active_positions + held_state.long_hold_positions}
        market_client = LiveMarketDataClient()
        lookback = max(universe_cfg.rsi_window_days + 1, universe_cfg.ma_long_window_days) + 5
        rsi_by_symbol: dict[str, float] = {}
        ma_trend_by_symbol: dict[str, bool | None] = {}
        for symbol in held_symbols:
            bars = market_client.fetch_daily_bars(symbol, lookback)
            closes = [bar.close for bar in bars]
            rsi_by_symbol[symbol] = relative_strength_index(closes, universe_cfg.rsi_window_days)
            ma_trend_by_symbol[symbol] = is_bullish_ma_trend(
                closes, universe_cfg.ma_short_window_days, universe_cfg.ma_long_window_days
            )
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE, cfg,
            rsi_by_symbol=rsi_by_symbol, ma_trend_by_symbol=ma_trend_by_symbol,
        )
```

This requires `ledger` to be imported in `cli.py` — check the current import line `from . import backtest_commands, commands` and change it to `from . import backtest_commands, commands, ledger`.

Finally, change the live `universe` dispatch (the final `else` branch) from:

```python
    else:
        universe_cfg = UniverseConfig()
        if args.mode:
            universe_cfg.ranking_mode = args.mode
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, SECTOR_CACHE_PATH, universe_cfg, today, args.refresh
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
                    "sector": c.sector,
                }
                for c in candidates
            ]
        }
```

to:

```python
    else:
        universe_cfg = UniverseConfig()
        if args.mode:
            universe_cfg.ranking_mode = args.mode
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, SECTOR_CACHE_PATH, universe_cfg, today, args.refresh
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
                    "sector": c.sector,
                    "rsi": c.rsi,
                    "ma_trend_bullish": c.ma_trend_bullish,
                }
                for c in candidates
            ]
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: PASS (all existing + 4 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: 100% GREEN — no exceptions, this is the last code task.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: wire --rsi/--ma-bullish flags and fetch fresh indicators in the state command"
```

---

### Task 8: `SKILL.md` documentation updates

**Files:**
- Modify: `.claude/skills/robinhood-trading/SKILL.md`

**Interfaces:**
- Consumes: nothing new — documents behavior already implemented in Tasks 1-7.
- Produces: nothing code-facing; no tests (matches this repo's existing precedent for doc-only SKILL.md updates).

- [ ] **Step 1: Update Step 2 (universe) to mention the new candidate fields**

In `.claude/skills/robinhood-trading/SKILL.md`, change:

```
This uses a weekly-cached membership list by default (fast). Only pass
`--refresh` if explicitly asked to force a refresh. Each candidate's
`sector` field (its GICS *industry* — e.g. "Semiconductors" or "Computer
Hardware", not the broader GICS sector — or `null` for the two leveraged
funds) is needed later in Step 7 when gating a BUY — no separate lookup
is required.
```

to:

```
This uses a weekly-cached membership list by default (fast). Only pass
`--refresh` if explicitly asked to force a refresh. Each candidate's
`sector` field (its GICS *industry* — e.g. "Semiconductors" or "Computer
Hardware", not the broader GICS sector — or `null` for the two leveraged
funds) is needed later in Step 7 when gating a BUY — no separate lookup
is required. Each candidate also carries `rsi` (14-day Relative Strength
Index) and `ma_trend_bullish` (whether the 5-day moving average is
currently above the 20-day moving average, or `null` if there isn't
enough history yet) — both also needed in Step 7.
```

- [ ] **Step 2: Update Step 6 to split discretionary exit guidance by lifecycle status**

Change:

```
For each symbol currently **held** (active or long-hold):
- Note its lifecycle `status` (`ACTIVE`, `WAITING`, `LONG_HOLD`) and
  `unrealized_pnl_pct` from Step 5.
- Consider a **discretionary early SELL** if a position has moved
  sharply against you — you don't have to wait out the full grace
  period if the decline looks decisive rather than noisy (see this
  session's backtest transcripts for worked examples of both calls).
- Otherwise, propose **HOLD** — the mechanical stop-loss/grace-period
  machinery and the weekly profit-goal sweep both run independently of
  this step and will catch what they're each designed to catch.
```

to:

```
For each symbol currently **held** (active or long-hold):
- Note its lifecycle `status` (`ACTIVE`, `WAITING`, `LONG_HOLD`),
  `unrealized_pnl_pct`, `rsi`, and `ma_trend_bullish` from Step 5.
- **ACTIVE/WAITING positions:** consider a discretionary early SELL if
  a position has moved sharply against you (you don't have to wait out
  the full grace period if the decline looks decisive rather than
  noisy — see this session's backtest transcripts for worked examples
  of both calls), or if RSI is deep in overbought territory, or if
  `ma_trend_bullish` has turned `false`.
- **LONG_HOLD positions:** these have no guaranteed recovery, so treat
  `ma_trend_bullish` turning `true` (a bounce back above the 20-day
  average) as a signal to consider **selling into the bounce** rather
  than holding out for a full recovery that may not come — this is
  often the best exit opportunity a long-hold position gets.
- Otherwise, propose **HOLD** — the mechanical stop-loss/grace-period
  machinery and the weekly profit-goal sweep both run independently of
  this step and will catch what they're each designed to catch.
```

- [ ] **Step 3: Update Step 7 to document the two new mechanical rejection reasons and required flags**

Change:

```
```
python -m robinhood_bot.cli risk-check buy SYMBOL --value <proposed dollar amount> --sector <symbol's sector from Step 2/Step 3 candidate data> --prices-json "<fresh quotes>"
python -m robinhood_bot.cli risk-check sell SYMBOL --prices-json "<fresh quotes>"
```

- If `"approved": false`, **do not execute this trade.** Read `"reason"`
  and either propose a smaller size / different symbol, or fall back to
  HOLD. Never override a rejection.
- A BUY is rejected if you already hold an active position in the same
  `--sector` (default limit: 1 position per sector) — the rejection
  `"reason"` names the sector; treat it exactly like any other
  rejection, never override it.
- For an approved BUY, `"max_position_value"` is the ceiling. Compute a
  whole-share quantity: `floor(min(proposed_value, max_position_value) /
  fresh_quote_price)`. You may propose fewer shares than the ceiling
  allows.
```

to:

```
```
python -m robinhood_bot.cli risk-check buy SYMBOL --value <proposed dollar amount> --sector <symbol's sector from Step 2/Step 3 candidate data> --rsi <symbol's rsi from Step 2/Step 3 candidate data> --ma-bullish/--no-ma-bullish (omit if ma_trend_bullish is null) --prices-json "<fresh quotes>"
python -m robinhood_bot.cli risk-check sell SYMBOL --prices-json "<fresh quotes>"
```

- If `"approved": false`, **do not execute this trade.** Read `"reason"`
  and either propose a smaller size / different symbol, or fall back to
  HOLD. Never override a rejection.
- A BUY is rejected if you already hold an active position in the same
  `--sector` (default limit: 1 position per sector) — the rejection
  `"reason"` names the sector; treat it exactly like any other
  rejection, never override it.
- A BUY is also rejected if the candidate's RSI is overbought (default
  threshold: 70), or if `ma_trend_bullish` is explicitly `false` (no
  confirmed short-term uptrend) — always pass `--rsi` from the
  candidate's data, and pass `--ma-bullish`/`--no-ma-bullish` only when
  `ma_trend_bullish` is `true`/`false`; omit the flag entirely when it's
  `null` (not enough history to judge — the check is skipped rather
  than blocking on missing data).
- For an approved BUY, `"max_position_value"` is the ceiling. Compute a
  whole-share quantity: `floor(min(proposed_value, max_position_value) /
  fresh_quote_price)`. You may propose fewer shares than the ceiling
  allows.
```

- [ ] **Step 4: Update Step 8 to pass `--rsi`/`--ma-bullish` on the paper and live fill commands**

Change:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --sector <same sector passed to Step 7's risk-check> --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote price> --reason "<why>"
```
```

to:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --sector <same sector passed to Step 7's risk-check> --rsi <same rsi passed to Step 7's risk-check> --ma-bullish/--no-ma-bullish (matching Step 7's risk-check, omit if null) --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote price> --reason "<why>"
```
```

Also update the live-mode fill example directly below it, from:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual fill price> --sector <same sector passed to Step 7's risk-check> --reason "<why>"
```
```

to:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual fill price> --sector <same sector passed to Step 7's risk-check> --rsi <same rsi passed to Step 7's risk-check> --ma-bullish/--no-ma-bullish (matching Step 7's risk-check, omit if null) --reason "<why>"
```
```

- [ ] **Step 5: Update the Backtest Mode section's Steps 7-8 bullet**

Change:

```
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --value <proposed dollar amount, for buys> --sector <symbol's sector,
  for buys> --prices-json "<quotes>"`, then on approval, `python -m
  robinhood_bot.cli backtest record-fill {buy|sell} SYMBOL --run RUN_ID
  --asof <simulated date> --qty <n> --price <quote price> --sector
  <same sector, for buys> --reason "<why>"`. There is no
  live-order-placement call in this mode, ever.
```

to:

```
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --value <proposed dollar amount, for buys> --sector <symbol's sector,
  for buys> --rsi <symbol's rsi, for buys> --ma-bullish/--no-ma-bullish
  (for buys, matching ma_trend_bullish, omit if null) --prices-json
  "<quotes>"`, then on approval, `python -m robinhood_bot.cli backtest
  record-fill {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --qty <n> --price <quote price> --sector <same sector, for buys>
  --rsi <same rsi, for buys> --ma-bullish/--no-ma-bullish (matching, for
  buys) --reason "<why>"`. There is no live-order-placement call in this
  mode, ever.
```

- [ ] **Step 6: Verify by reading the file back**

Re-read `.claude/skills/robinhood-trading/SKILL.md` in full and confirm all edits landed cleanly and nothing else changed.

- [ ] **Step 7: Run the full suite one more time**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (doc-only change, no test impact — final confirmation before finishing the branch).

- [ ] **Step 8: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md
git commit -m "docs: document RSI/MA-crossover flags and lifecycle-specific exit guidance"
```
