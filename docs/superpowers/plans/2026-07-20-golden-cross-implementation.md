# Golden Cross (50/200-Day SMA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 50-day-vs-200-day SMA "golden cross / death cross" trend regime signal, reusing the existing `is_bullish_ma_trend` function with wider windows, that mechanically gates BUYs (same shape as the existing 5/20-day MA-trend check) and informs LONG_HOLD exit judgment (same shape as the existing "sell into the bounce" guidance).

**Architecture:** `Candidate` and `Position` each gain a new `golden_cross_bullish: bool | None` field computed via `is_bullish_ma_trend(closes, 50, 200)`. It flows through the exact same pipeline the existing `ma_trend_bullish` field already uses end-to-end: `build_universe` → `evaluate_buy` (new rejection check) → `cmd_risk_check`/`cmd_record_fill`/`cmd_state` → `backtest_commands` equivalents → `cli.py` flags/dispatch → `SKILL.md` guidance.

**Tech Stack:** Python 3.11+, pytest, existing `robinhood_bot` package — no new dependencies.

## Global Constraints

- Reuse `is_bullish_ma_trend(closes, short_window, long_window)` from `universe.py` unchanged — do not write a new indicator function. Call it a second time with `(50, 200)` alongside the existing `(5, 20)` call.
- New config: `UniverseConfig.golden_cross_short_window_days: int = 50`, `UniverseConfig.golden_cross_long_window_days: int = 200`. No new `RiskConfig` threshold is needed — this is a boolean gate like `ma_trend_bullish`, not a percentage threshold like `rsi_overbought_threshold`.
- Field/param name everywhere: `golden_cross_bullish` (bool | None). CLI flag: `--golden-cross-bullish`/`--no-golden-cross-bullish` (tri-state, `argparse.BooleanOptionalAction`, default `None`).
- `evaluate_buy` gains one new **required** parameter `golden_cross_bullish: bool | None`, placed immediately after the existing `ma_trend_bullish` parameter. Every existing call site (there are 15 in `tests/test_risk_engine.py`, one in `commands.py`, one in `backtest_commands.py`) must be updated to pass it.
- The gate check: `if golden_cross_bullish is False: return BuyDecision(False, "long-term trend bearish (50-day SMA at or below 200-day SMA / death cross)", max_value)`, placed immediately after the existing `ma_trend_bullish is False` check. `golden_cross_bullish is False` (not falsy) — `None` always bypasses.
- `cmd_risk_check`/`cmd_record_fill` (both live and backtest variants) gain an **optional** `golden_cross_bullish: bool | None = None` parameter, placed immediately after the existing `ma_trend_bullish` parameter — existing callers/tests that omit it are unaffected.
- Lookback buffers that currently use `cfg.ma_long_window_days` (20) or the hardcoded `ma_long_window_days` value (20) as their max must be widened to also cover `golden_cross_long_window_days` (200) — this is the single largest window in the system now.
- Every step that writes code must be followed by running the exact test command shown and confirming the exact expected result before moving on.

---

### Task 1: `universe.py` — config, `Candidate` field, `build_universe` computation

**Files:**
- Modify: `robinhood_bot/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: existing `is_bullish_ma_trend(closes, short_window, long_window) -> bool | None` (unchanged).
- Produces: `UniverseConfig.golden_cross_short_window_days: int = 50`, `UniverseConfig.golden_cross_long_window_days: int = 200`; `Candidate.golden_cross_bullish: bool | None = None`, populated by `build_universe`. Later tasks (`Position`, `evaluate_buy`, `cli.py`) all consume this field name.

- [ ] **Step 1: Write the failing config-defaults test**

Add these two lines to the existing `test_universe_config_defaults` test in `tests/test_universe.py` (after the `assert cfg.ranking_mode == "both"` line):

```python
    assert cfg.golden_cross_short_window_days == 50
    assert cfg.golden_cross_long_window_days == 200
```

- [ ] **Step 2: Write the failing `build_universe` field test**

Add this new test function to `tests/test_universe.py`, immediately after `test_build_universe_includes_rsi_and_ma_trend_on_candidate`:

```python
def test_build_universe_includes_golden_cross_on_candidate(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    bars = [Bar(101.0, 99.0, 100.0 + i * 0.1) for i in range(201)]
    client = FakeMarketDataClient(
        sp500=["A"], nasdaq100=[],
        market_caps={"A": 100.0},
        bars={"A": bars},
        sectors={"A": "Healthcare"},
    )
    cfg = UniverseConfig(top_n_sp500=1, top_n_nasdaq100=1, leveraged_funds=[])

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert candidates[0].golden_cross_bullish is True


def test_build_universe_golden_cross_none_with_insufficient_history(tmp_path):
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

    assert candidates[0].golden_cross_bullish is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_universe.py -k "golden_cross" -v`
Expected: 3 FAILures — `test_universe_config_defaults` fails on the new `assert`s (`AttributeError`), the two new tests fail with `AttributeError: 'Candidate' object has no attribute 'golden_cross_bullish'`.

- [ ] **Step 4: Implement in `universe.py`**

In `UniverseConfig`, add two fields immediately after `ma_long_window_days: int = 20`:

```python
    ma_long_window_days: int = 20
    golden_cross_short_window_days: int = 50
    golden_cross_long_window_days: int = 200
    cache_max_age_days: int = 7
```

In `Candidate`, add one field immediately after `ma_trend_bullish: bool | None = None`:

```python
    ma_trend_bullish: bool | None = None
    golden_cross_bullish: bool | None = None
```

In `build_universe`, widen the `lookback` line to also cover the new window:

```python
    lookback = max(
        cfg.realized_vol_window_days, cfg.atr_window_days, cfg.rsi_window_days + 1,
        cfg.ma_long_window_days, cfg.golden_cross_long_window_days,
    ) + 1
```

Add a `golden_crosses` dict alongside the existing `ma_trends` dict:

```python
    realized_vols: dict[str, float] = {}
    atr_pcts: dict[str, float] = {}
    rsis: dict[str, float] = {}
    ma_trends: dict[str, bool | None] = {}
    golden_crosses: dict[str, bool | None] = {}

    for member in all_members:
        bars = client.fetch_daily_bars(member.symbol, lookback)
        if not bars:
            continue
        closes = [bar.close for bar in bars]
        realized_vols[member.symbol] = realized_volatility(closes[-(cfg.realized_vol_window_days + 1):])
        atr_pcts[member.symbol] = average_true_range_pct(bars[-(cfg.atr_window_days + 1):])
        rsis[member.symbol] = relative_strength_index(closes, cfg.rsi_window_days)
        ma_trends[member.symbol] = is_bullish_ma_trend(closes, cfg.ma_short_window_days, cfg.ma_long_window_days)
        golden_crosses[member.symbol] = is_bullish_ma_trend(
            closes, cfg.golden_cross_short_window_days, cfg.golden_cross_long_window_days
        )
```

And add the field to the `Candidate(...)` construction:

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
            golden_cross_bullish=golden_crosses[member.symbol],
        ))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_universe.py -v`
Expected: all PASS (including the pre-existing tests in this file — confirm no regressions).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: compute golden-cross (50/200-day SMA) signal on universe candidates"
```

---

### Task 2: `portfolio_state.py` + `ledger.py` — `Position` field and persistence

**Files:**
- Modify: `robinhood_bot/portfolio_state.py`
- Modify: `robinhood_bot/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Position.golden_cross_bullish: bool | None = None`, persisted/read by `ledger.py`. Task 4 (`commands.py`) will set this at buy time.

- [ ] **Step 1: Write the failing round-trip test**

Add this test to `tests/test_ledger.py`, immediately after `test_save_and_load_round_trip_preserves_rsi_and_ma_trend`:

```python
def test_save_and_load_round_trip_preserves_golden_cross(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(
        cash=8_000.0,
        active_positions=[
            Position(
                "AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE,
                golden_cross_bullish=True,
            )
        ],
    )
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].golden_cross_bullish is True
```

Add this test immediately after `test_load_state_defaults_missing_rsi_and_ma_trend_to_none_for_old_ledger_files`:

```python
def test_load_state_defaults_missing_golden_cross_to_none_for_old_ledger_files(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "cash": 5_000.0,
        "active_positions": [{
            "symbol": "AAPL", "qty": 10, "entry_price": 100.0,
            "entry_date": "2026-07-01", "status": "ACTIVE", "underwater_since": None,
            "sector": None, "rsi": None, "ma_trend_bullish": None,
        }],
        "long_hold_positions": [],
        "month": "", "month_start_equity": 0.0, "week": "", "week_realized_pnl": 0.0,
        "prior_week_realized_pnl": 0.0,
    }))

    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].golden_cross_bullish is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_ledger.py -k golden_cross -v`
Expected: 2 FAILures — `TypeError: Position.__init__() got an unexpected keyword argument 'golden_cross_bullish'` for the first, `AttributeError` for the second.

- [ ] **Step 3: Implement in `portfolio_state.py`**

Add one field to `Position`, immediately after `ma_trend_bullish: bool | None = None`:

```python
    rsi: float | None = None
    ma_trend_bullish: bool | None = None
    golden_cross_bullish: bool | None = None
```

- [ ] **Step 4: Implement in `ledger.py`**

In `_position_to_dict`, add one line after `"ma_trend_bullish": position.ma_trend_bullish,`:

```python
        "rsi": position.rsi,
        "ma_trend_bullish": position.ma_trend_bullish,
        "golden_cross_bullish": position.golden_cross_bullish,
    }
```

In `_position_from_dict`, add one line after `ma_trend_bullish=data.get("ma_trend_bullish"),`:

```python
        rsi=data.get("rsi"),
        ma_trend_bullish=data.get("ma_trend_bullish"),
        golden_cross_bullish=data.get("golden_cross_bullish"),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_ledger.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/portfolio_state.py robinhood_bot/ledger.py tests/test_ledger.py
git commit -m "feat: persist a position's entry-time golden-cross reading in the ledger"
```

---

### Task 3: `risk_engine.py` — `evaluate_buy` gate and every existing call site

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `Position`/`PortfolioState` (unchanged).
- Produces: `evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector, rsi, ma_trend_bullish, golden_cross_bullish) -> BuyDecision`. Task 4/5 (`commands.py`, `backtest_commands.py`) both call this and must pass the new argument.

- [ ] **Step 1: Update every existing `evaluate_buy` call site in the test file**

In `tests/test_risk_engine.py`, every existing call to `evaluate_buy(...)` needs `golden_cross_bullish=None` added as the last keyword argument (this keeps every pre-existing test's behavior unchanged — `None` always bypasses the new check). There are 15 call sites; add `, golden_cross_bullish=None` (or on its own line matching the call's existing multi-line style) to each of these test functions:

- `test_evaluate_buy_rejects_when_symbol_already_held`
- `test_evaluate_buy_rejects_when_circuit_breaker_tripped`
- `test_evaluate_buy_rejects_when_no_active_slots`
- `test_evaluate_buy_rejects_when_oversized`
- `test_evaluate_buy_rejects_when_insufficient_cash`
- `test_evaluate_buy_approves_happy_path`
- `test_evaluate_buy_rejects_when_sector_concentration_limit_reached`
- `test_evaluate_buy_approves_when_different_sector_held`
- `test_evaluate_buy_approves_when_sector_none_bypasses_concentration_check`
- `test_evaluate_buy_approves_when_bonus_slot_from_prior_week_surplus_allows_it`
- `test_evaluate_buy_rejects_when_even_boosted_effective_cap_is_reached`
- `test_evaluate_buy_rejects_when_overbought`
- `test_evaluate_buy_rejects_when_no_confirmed_uptrend`
- `test_evaluate_buy_approves_when_ma_trend_unknown_bypasses_check`
- `test_evaluate_buy_approves_at_exact_rsi_threshold`

For example, `test_evaluate_buy_rejects_when_symbol_already_held`'s call becomes:

```python
    decision = evaluate_buy(state, "AAPL", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
```

And the multi-line calls (e.g. `test_evaluate_buy_rejects_when_overbought`) become:

```python
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=75.0, ma_trend_bullish=True, golden_cross_bullish=None,
    )
```

Apply the same `, golden_cross_bullish=None` (or `golden_cross_bullish=True` where noted below) addition to all 15 sites — every existing site gets `golden_cross_bullish=None` **except** these two, which get `golden_cross_bullish=True` so they keep testing what they were built to test without the new check interfering:

- `test_evaluate_buy_rejects_when_overbought` → `golden_cross_bullish=True`
- `test_evaluate_buy_approves_at_exact_rsi_threshold` → `golden_cross_bullish=True`

(These two already set `ma_trend_bullish=True` for the same reason — an unrelated check shouldn't accidentally cause the rejection/approval this test is actually checking. `golden_cross_bullish=None` would also bypass safely, but matching the existing `ma_trend_bullish=True` pattern keeps the two checks visually consistent in these two tests.)

- [ ] **Step 2: Write the three new failing tests**

Add these three tests to `tests/test_risk_engine.py`, immediately after `test_evaluate_buy_approves_at_exact_rsi_threshold`:

```python
def test_evaluate_buy_rejects_when_death_cross():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=True, golden_cross_bullish=False,
    )
    assert decision.approved is False
    assert "death cross" in decision.reason


def test_evaluate_buy_approves_when_golden_cross_unknown_bypasses_check():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=True, golden_cross_bullish=None,
    )
    assert decision.approved is True


def test_evaluate_buy_approves_when_golden_cross_bullish():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=True, golden_cross_bullish=True,
    )
    assert decision.approved is True
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_risk_engine.py -v`
Expected: every `evaluate_buy` call (updated and new) fails with `TypeError: evaluate_buy() missing 1 required positional argument: 'golden_cross_bullish'` or `TypeError: evaluate_buy() got an unexpected keyword argument 'golden_cross_bullish'`.

- [ ] **Step 4: Implement in `risk_engine.py`**

Update the `evaluate_buy` signature, adding the parameter immediately after `ma_trend_bullish`:

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
    golden_cross_bullish: bool | None,
) -> BuyDecision:
```

Add the new check immediately after the existing `ma_trend_bullish is False` check:

```python
    if ma_trend_bullish is False:
        return BuyDecision(False, "no confirmed short-term uptrend (short MA at or below long MA)", max_value)

    if golden_cross_bullish is False:
        return BuyDecision(
            False,
            "long-term trend bearish (50-day SMA at or below 200-day SMA / death cross)",
            max_value,
        )

    if circuit_breaker_tripped(state.month_start_equity, total_equity, cfg):
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_risk_engine.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: gate BUYs on the golden-cross (50/200-day SMA) trend regime"
```

---

### Task 4: `commands.py` — wiring for `cmd_risk_check`, `cmd_record_fill`, `cmd_state`

**Files:**
- Modify: `robinhood_bot/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `evaluate_buy(..., golden_cross_bullish)` from Task 3; `Position.golden_cross_bullish` from Task 2.
- Produces: `cmd_risk_check(..., golden_cross_bullish=None)`, `cmd_record_fill(..., golden_cross_bullish=None)` (persists onto the new `Position`), `cmd_state(..., golden_cross_by_symbol=None)` → `_position_summary` includes a fresh `"golden_cross_bullish"` field. Task 5 (`backtest_commands.py`) and Task 6 (`cli.py`) both call these.

- [ ] **Step 1: Write the failing tests**

Add this test to `tests/test_commands.py`, immediately after `test_cmd_state_includes_fresh_rsi_and_ma_trend_for_held_positions`:

```python
def test_cmd_state_includes_fresh_golden_cross_for_held_positions(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, rsi=50.0)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
        rsi_by_symbol={"AAPL": 81.3}, ma_trend_by_symbol={"AAPL": False},
        golden_cross_by_symbol={"AAPL": True},
    )

    assert result["active_positions"][0]["golden_cross_bullish"] is True
```

Add this test immediately after `test_cmd_state_defaults_rsi_and_ma_trend_when_not_supplied`:

```python
def test_cmd_state_defaults_golden_cross_to_none_when_not_supplied(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
    )

    assert result["active_positions"][0]["golden_cross_bullish"] is None
```

Add this test immediately after `test_cmd_record_fill_buy_persists_rsi_and_ma_trend`:

```python
def test_cmd_record_fill_buy_persists_golden_cross(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle",
        rsi=62.5, ma_trend_bullish=True, golden_cross_bullish=True,
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].golden_cross_bullish is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_commands.py -k golden_cross -v`
Expected: 3 FAILures — `TypeError: cmd_state() got an unexpected keyword argument 'golden_cross_by_symbol'` for the first, `KeyError: 'golden_cross_bullish'` for the second, `TypeError: cmd_record_fill() got an unexpected keyword argument 'golden_cross_bullish'` for the third.

- [ ] **Step 3: Implement in `commands.py`**

Update `_position_summary`'s signature and body:

```python
def _position_summary(
    position, prices: dict[str, float], rsi_by_symbol: dict[str, float], ma_trend_by_symbol: dict[str, bool | None],
    golden_cross_by_symbol: dict[str, bool | None],
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
        "golden_cross_bullish": golden_cross_by_symbol.get(position.symbol),
    }
```

Update `cmd_state`'s signature and the two `_position_summary` call sites:

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
    golden_cross_by_symbol: dict[str, bool | None] | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    rsi_by_symbol = rsi_by_symbol or {}
    ma_trend_by_symbol = ma_trend_by_symbol or {}
    golden_cross_by_symbol = golden_cross_by_symbol or {}

    active_out = [
        _position_summary(p, prices, rsi_by_symbol, ma_trend_by_symbol, golden_cross_by_symbol)
        for p in state.active_positions
    ]
    long_hold_out = [
        _position_summary(p, prices, rsi_by_symbol, ma_trend_by_symbol, golden_cross_by_symbol)
        for p in state.long_hold_positions
    ]
```

Update `cmd_risk_check`'s signature and its `evaluate_buy` call:

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
    golden_cross_bullish: bool | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    positions_value = sum(
        prices.get(p.symbol, p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    total_equity = state.cash + positions_value

    if action == "buy":
        decision = evaluate_buy(
            state, symbol, proposed_value, total_equity, cfg, sector, rsi, ma_trend_bullish, golden_cross_bullish,
        )
```

Update `cmd_record_fill`'s signature and the `Position(...)` construction:

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
    golden_cross_bullish: bool | None = None,
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
                golden_cross_bullish=golden_cross_bullish,
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_commands.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: wire golden-cross through cmd_risk_check, cmd_record_fill, cmd_state"
```

---

### Task 5: `backtest_commands.py` — wiring and integration test

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Test: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `commands.cmd_state(..., golden_cross_by_symbol)`, `commands.cmd_risk_check(..., golden_cross_bullish)`, `commands.cmd_record_fill(..., golden_cross_bullish)` from Task 4; `evaluate_buy(..., golden_cross_bullish)` from Task 3; `is_bullish_ma_trend` (already imported).
- Produces: `cmd_backtest_state(..., golden_cross_short_window_days=50, golden_cross_long_window_days=200)`, `cmd_backtest_risk_check(..., golden_cross_bullish=None)`, `cmd_backtest_record_fill(..., golden_cross_bullish=None)`, `cmd_backtest_run(..., golden_cross_short_window_days=50, golden_cross_long_window_days=200)` computing it fresh per candidate per day. Task 6 (`cli.py`) calls all of these.

- [ ] **Step 1: Write the failing unit tests**

Add this test to `tests/test_backtest_commands.py`, immediately after `test_cmd_backtest_state_includes_fresh_rsi_for_held_position`:

```python
def test_cmd_backtest_state_includes_fresh_golden_cross_for_held_position(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2025, 6, 1), PositionStatus.ACTIVE)],
    ))

    bars = [
        HistoricalBar(date(2025, 6, 1) + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i)
        for i in range(201)
    ]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    asof = bars[-1].date

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 300.0}, asof=asof,
        cfg=RiskConfig(), store=store,
    )

    assert result["active_positions"][0]["golden_cross_bullish"] is True
```

- [ ] **Step 2: Write the failing integration test**

Add this test to `tests/test_backtest_commands.py`, immediately after `test_cmd_backtest_run_rejects_overbought_candidate_for_next_ranked`:

```python
def test_cmd_backtest_run_rejects_death_cross_candidate_for_next_ranked(tmp_path):
    # AAPL2 declines steadily for 200 days (500.0 down by 1.5/day to 201.5),
    # then has a choppy-but-net-rising 50-day tail (net +0.4/day drift with
    # alternating +0.5/-0.9 noise). That tail keeps its 14-day RSI at ~64.3
    # (not overbought) and its 5-day SMA above its 20-day SMA (confirmed
    # short-term uptrend, not rejected by the existing MA-trend check) --
    # but its 50-day SMA (~164, entirely within the mild recovery) is still
    # far below its 200-day SMA (~267, dominated by the steep decline), so
    # it must be REJECTED by the new golden-cross gate specifically.
    #
    # JPM drifts gently upward the whole time (+0.01/day) with a small
    # alternating +/-0.02 wobble -- enough real up/down movement to keep its
    # 14-day RSI at ~62.5 (not overbought) rather than pinned to 100 by a
    # purely monotonic series, while its volatility/ATR over the last 3 days
    # (~0.0059 / ~0.00066) stay far below AAPL2's (~0.143 / ~0.011), so JPM
    # ranks second. Its own 5/20 and 50/200 SMA checks are both `True` (the
    # gentle drift keeps recent averages above older ones), so JPM passes
    # every gate cleanly and is the one that should actually get bought. If
    # the golden-cross gate weren't wired into this loop, AAPL2 (the
    # top-ranked candidate on volatility/ATR) would be bought instead.
    closes = [500.0 - i * 1.5 for i in range(200)]
    base = closes[-1]
    for i in range(50):
        drift = base + i * 0.4
        noise = 0.5 if i % 2 == 0 else -0.9
        closes.append(drift + noise)

    start_date = date(2025, 6, 1)
    aapl2_bars = [
        HistoricalBar(start_date + timedelta(days=i), c + 1.0, c + 1.0, c - 1.0, c)
        for i, c in enumerate(closes)
    ]
    end_date = start_date + timedelta(days=len(closes) - 1)
    jpm_closes = [150.0 + i * 0.01 + (0.02 if i % 2 == 0 else -0.02) for i in range(len(closes))]
    jpm_bars = [
        HistoricalBar(start_date + timedelta(days=i), c + 0.05, c + 0.05, c - 0.05, c)
        for i, c in enumerate(jpm_closes)
    ]
    bars = {
        "SPY": [HistoricalBar(end_date, 400.0, 401.0, 399.0, 400.0)],
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
        "run_golden", tmp_path, starting_cash=10_000.0, start=end_date, end=end_date,
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={}, store=store, cfg=cfg,
        vol_window_days=2, atr_window_days=2,
    )

    paths = backtest_commands.resolve_run_paths("run_golden", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    assert [p.symbol for p in final_state.active_positions] == ["JPM"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backtest_commands.py -k "golden_cross or death_cross" -v`
Expected: `test_cmd_backtest_state_includes_fresh_golden_cross_for_held_position` FAILs with `KeyError: 'golden_cross_bullish'`. `test_cmd_backtest_run_rejects_death_cross_candidate_for_next_ranked` FAILs — `final_state.active_positions` holds `["AAPL2"]`, not `["JPM"]` (confirm this by reading the assertion failure output, since the gate doesn't exist yet).

- [ ] **Step 4: Implement in `backtest_commands.py`**

Update `cmd_backtest_state`'s signature and body:

```python
def cmd_backtest_state(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
    cfg: RiskConfig, store: HistoricalPriceStore,
    rsi_window_days: int = 14, ma_short_window_days: int = 5, ma_long_window_days: int = 20,
    golden_cross_short_window_days: int = 50, golden_cross_long_window_days: int = 200,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    state = ledger.load_state(paths.ledger, starting_cash)
    held_symbols = {p.symbol for p in state.active_positions + state.long_hold_positions}

    lookback = max(rsi_window_days + 1, ma_long_window_days, golden_cross_long_window_days)
    rsi_by_symbol: dict[str, float] = {}
    ma_trend_by_symbol: dict[str, bool | None] = {}
    golden_cross_by_symbol: dict[str, bool | None] = {}
    for symbol in held_symbols:
        closes = store.get_closes_window(symbol, asof, lookback)
        rsi_by_symbol[symbol] = relative_strength_index(closes, rsi_window_days)
        ma_trend_by_symbol[symbol] = is_bullish_ma_trend(closes, ma_short_window_days, ma_long_window_days)
        golden_cross_by_symbol[symbol] = is_bullish_ma_trend(
            closes, golden_cross_short_window_days, golden_cross_long_window_days
        )

    return commands.cmd_state(
        paths.ledger, starting_cash, prices, asof, trading_mode="backtest", cfg=cfg,
        rsi_by_symbol=rsi_by_symbol, ma_trend_by_symbol=ma_trend_by_symbol,
        golden_cross_by_symbol=golden_cross_by_symbol,
    )
```

Update `cmd_backtest_risk_check`'s signature and its `commands.cmd_risk_check` call:

```python
def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig, sector: str | None = None,
    rsi: float = 50.0, ma_trend_bullish: bool | None = None, golden_cross_bullish: bool | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(
        paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg, sector, rsi,
        ma_trend_bullish, golden_cross_bullish,
    )
```

Update `cmd_backtest_record_fill`'s signature and its `commands.cmd_record_fill` call:

```python
def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str, sector: str | None = None,
    rsi: float = 50.0, ma_trend_bullish: bool | None = None, golden_cross_bullish: bool | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason, sector,
        rsi, ma_trend_bullish, golden_cross_bullish,
    )
```

Update `cmd_backtest_run`'s signature (add the two new window parameters), the indicator-lookback widening, and the `evaluate_buy`/`cmd_record_fill` calls in the entries loop:

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
    golden_cross_short_window_days: int = 50,
    golden_cross_long_window_days: int = 200,
) -> dict:
```

In the entries loop, replace the indicator computation and `evaluate_buy`/`cmd_record_fill` calls with:

```python
                indicator_lookback = max(rsi_window_days + 1, ma_long_window_days, golden_cross_long_window_days)
                closes = store.get_closes_window(symbol, today, indicator_lookback)
                rsi = relative_strength_index(closes, rsi_window_days)
                ma_trend_bullish = is_bullish_ma_trend(closes, ma_short_window_days, ma_long_window_days)
                golden_cross_bullish = is_bullish_ma_trend(
                    closes, golden_cross_short_window_days, golden_cross_long_window_days
                )
                decision = evaluate_buy(
                    state, symbol, proposed_value, total_equity, cfg, sector, rsi, ma_trend_bullish,
                    golden_cross_bullish,
                )
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry", sector, rsi, ma_trend_bullish, golden_cross_bullish,
                )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_backtest_commands.py -v`
Expected: all PASS, including `test_cmd_backtest_run_rejects_death_cross_candidate_for_next_ranked` now holding `["JPM"]`.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: wire golden-cross through backtest_commands (state, risk-check, record-fill, run)"
```

---

### Task 6: `cli.py` — flags, `universe` output, live `state` dispatch

**Files:**
- Modify: `robinhood_bot/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_universe` (Task 1) producing `Candidate.golden_cross_bullish`; `commands.cmd_risk_check`/`cmd_record_fill`/`cmd_state` (Task 4); `backtest_commands.cmd_backtest_risk_check`/`cmd_backtest_record_fill`/`cmd_backtest_state` (Task 5).
- Produces: `--golden-cross-bullish`/`--no-golden-cross-bullish` CLI flags on `risk-check`/`record-fill` (live and backtest); `universe` output includes `golden_cross_bullish` per candidate; live `state` dispatch fetches and passes `golden_cross_by_symbol`.

- [ ] **Step 1: Write the failing tests**

Update the existing `test_cli_universe_command_prints_json` test in `tests/test_cli.py` — change the `fake_candidates` line and add one assertion:

```python
    fake_candidates = [
        universe.Candidate(
            "AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0, sector="Technology", rsi=62.0,
            ma_trend_bullish=True, golden_cross_bullish=True,
        ),
    ]
```

```python
    assert output["candidates"][0]["golden_cross_bullish"] is True
```

Add this test to `tests/test_cli.py`, immediately after `test_cli_risk_check_buy_passes_rsi_and_ma_bullish_flags`:

```python
def test_cli_risk_check_buy_passes_golden_cross_bullish_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["golden_cross_bullish"] = golden_cross_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main([
        "risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}",
        "--golden-cross-bullish",
    ])

    assert exit_code == 0
    assert captured["golden_cross_bullish"] is True


def test_cli_risk_check_buy_defaults_golden_cross_bullish_to_none_when_omitted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["golden_cross_bullish"] = golden_cross_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main(["risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}"])

    assert exit_code == 0
    assert captured["golden_cross_bullish"] is None
```

Add this test immediately after `test_cli_backtest_risk_check_passes_rsi_and_ma_bullish_flags`:

```python
def test_cli_backtest_risk_check_passes_golden_cross_bullish_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    captured = {}

    def fake_cmd_backtest_risk_check(
        run_id, base_dir, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["golden_cross_bullish"] = golden_cross_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_risk_check", fake_cmd_backtest_risk_check)

    exit_code = cli.main([
        "backtest", "risk-check", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--value", "500", "--prices-json", "{}", "--no-golden-cross-bullish",
    ])

    assert exit_code == 0
    assert captured["golden_cross_bullish"] is False
```

Add this test immediately after `test_cli_state_command_fetches_indicators_for_held_positions`:

```python
def test_cli_state_command_fetches_golden_cross_for_held_positions(tmp_path, monkeypatch, capsys):
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
            return [Bar(101.0 + i * 0.1, 99.0 + i * 0.1, 100.0 + i * 0.1) for i in range(201)]

    monkeypatch.setattr(cli, "LiveMarketDataClient", FakeClient)

    exit_code = cli.main(["state", "--prices-json", '{"AAPL": 124.0}'])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["active_positions"][0]["golden_cross_bullish"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -k golden_cross -v`
Expected: FAILures — `AttributeError`/`KeyError` for the universe test (no `golden_cross_bullish` on `Candidate`... note: this will actually pass once Task 1 lands, since `Candidate` already has the field by now — but `cli.py`'s `universe` dispatch doesn't include it in its output dict yet, so the assertion `KeyError`s there). `--golden-cross-bullish`/`--no-golden-cross-bullish` tests fail with `SystemExit`/argparse error (unrecognized argument). The live-state test fails with `KeyError: 'golden_cross_bullish'`.

- [ ] **Step 3: Implement in `cli.py`**

Import `is_bullish_ma_trend` is already imported; no new import needed.

Add the flag to all four argparse parsers that already have `--ma-bullish` (`p_risk`, `p_fill`, `p_bt_risk`, `p_bt_fill`), immediately after each existing `--ma-bullish` line:

```python
    p_risk.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_risk.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)
```

```python
    p_fill.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_fill.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)
```

```python
    p_bt_risk.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_bt_risk.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)
```

```python
    p_bt_fill.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_bt_fill.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)
```

Update `_dispatch_backtest`'s `risk-check` and `record-fill` branches:

```python
    if args.backtest_command == "risk-check":
        return backtest_commands.cmd_backtest_risk_check(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
    if args.backtest_command == "record-fill":
        return backtest_commands.cmd_backtest_record_fill(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, date.fromisoformat(args.asof), args.reason, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
```

Update the live `state` dispatch to fetch and pass `golden_cross_by_symbol`:

```python
    if args.command == "state":
        universe_cfg = UniverseConfig()
        held_state = ledger.load_state(LEDGER_PATH, STARTING_CASH)
        held_symbols = {p.symbol for p in held_state.active_positions + held_state.long_hold_positions}
        market_client = LiveMarketDataClient()
        lookback = max(
            universe_cfg.rsi_window_days + 1, universe_cfg.ma_long_window_days,
            universe_cfg.golden_cross_long_window_days,
        ) + 5
        rsi_by_symbol: dict[str, float] = {}
        ma_trend_by_symbol: dict[str, bool | None] = {}
        golden_cross_by_symbol: dict[str, bool | None] = {}
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
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE, cfg,
            rsi_by_symbol=rsi_by_symbol, ma_trend_by_symbol=ma_trend_by_symbol,
            golden_cross_by_symbol=golden_cross_by_symbol,
        )
```

Update the live `risk-check` and `record-fill` dispatch branches:

```python
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
```

Update the `universe` result-building dict comprehension:

```python
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
                    "golden_cross_bullish": c.golden_cross_bullish,
                }
                for c in candidates
            ]
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS (should be 203 pre-existing + roughly 16 new = ~219 tests; confirm the exact count in the output and that there are zero failures).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: add --golden-cross-bullish CLI flag and wire it through universe/state/risk-check/record-fill"
```

---

### Task 7: `SKILL.md` — document the new signal

**Files:**
- Modify: `.claude/skills/robinhood-trading/SKILL.md`

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: nothing consumed by other tasks — this is the last task.

- [ ] **Step 1: Update Step 2 (universe) to document the new field**

In the paragraph ending "...both also needed in Step 7.", change it to also mention the new field:

```markdown
This uses a weekly-cached membership list by default (fast). Only pass
`--refresh` if explicitly asked to force a refresh. Each candidate's
`sector` field (its GICS *industry* — e.g. "Semiconductors" or "Computer
Hardware", not the broader GICS sector — or `null` for the two leveraged
funds) is needed later in Step 7 when gating a BUY — no separate lookup
is required. Each candidate also carries `rsi` (14-day Relative Strength
Index), `ma_trend_bullish` (whether the 5-day moving average is
currently above the 20-day moving average, or `null` if there isn't
enough history yet), and `golden_cross_bullish` (the same check on the
50-day vs 200-day moving average — a longer-horizon trend regime read,
`null` if there isn't 200 days of history yet) — all three also needed
in Step 7.
```

- [ ] **Step 2: Update Step 6's per-held-symbol bullet list**

Change the "Note its lifecycle..." bullet and the two status-specific bullets:

```markdown
For each symbol currently **held** (active or long-hold):
- Note its lifecycle `status` (`ACTIVE`, `WAITING`, `LONG_HOLD`),
  `unrealized_pnl_pct`, `rsi`, `ma_trend_bullish`, and
  `golden_cross_bullish` from Step 5.
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
  often the best exit opportunity a long-hold position gets. A
  `golden_cross_bullish` flip to `true` (the 50-day average moving back
  above the 200-day average) is a **stronger, higher-conviction**
  version of the same signal — it's a slower-moving, more durable read
  than the 5/20 check, which can reverse within days in a choppy
  market. When both are `true` at once, that's the clearest case for
  selling into the bounce; a `ma_trend_bullish`-only flip is a weaker,
  more provisional read worth weighing against how deep the position is
  underwater.
```

- [ ] **Step 3: Update Step 7's BUY-gate documentation**

Update the risk-check command example:

```markdown
python -m robinhood_bot.cli risk-check buy SYMBOL --value <proposed dollar amount> --sector <symbol's sector from Step 2/Step 3 candidate data> --rsi <symbol's rsi from Step 2/Step 3 candidate data> --ma-bullish/--no-ma-bullish (omit if ma_trend_bullish is null) --golden-cross-bullish/--no-golden-cross-bullish (omit if golden_cross_bullish is null) --prices-json "<fresh quotes>"
```

Update the rejection-reasons bullet:

```markdown
- A BUY is also rejected if the candidate's RSI is overbought (default
  threshold: 70), if `ma_trend_bullish` is explicitly `false` (no
  confirmed short-term uptrend), or if `golden_cross_bullish` is
  explicitly `false` (death cross — the 50-day average at or below the
  200-day average) — always pass `--rsi` from the candidate's data, and
  pass `--ma-bullish`/`--no-ma-bullish` and
  `--golden-cross-bullish`/`--no-golden-cross-bullish` only when the
  corresponding field is `true`/`false`; omit each flag entirely when
  it's `null` (not enough history to judge — the check is skipped
  rather than blocking on missing data).
```

- [ ] **Step 4: Update Step 8's record-fill examples**

Update both `record-fill buy` command examples (paper mode and live mode):

```markdown
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --sector <same sector passed to Step 7's risk-check> --rsi <same rsi passed to Step 7's risk-check> --ma-bullish/--no-ma-bullish (matching Step 7's risk-check, omit if null) --golden-cross-bullish/--no-golden-cross-bullish (matching Step 7's risk-check, omit if null) --reason "<why>"
```

```markdown
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual fill price> --sector <same sector passed to Step 7's risk-check> --rsi <same rsi passed to Step 7's risk-check> --ma-bullish/--no-ma-bullish (matching Step 7's risk-check, omit if null) --golden-cross-bullish/--no-golden-cross-bullish (matching Step 7's risk-check, omit if null) --reason "<why>"
```

- [ ] **Step 5: Update the Backtest Mode section's Steps 7-8 bullet**

```markdown
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --value <proposed dollar amount, for buys> --sector <symbol's sector,
  for buys> --rsi <symbol's rsi, for buys> --ma-bullish/--no-ma-bullish
  (for buys, matching ma_trend_bullish, omit if null)
  --golden-cross-bullish/--no-golden-cross-bullish (for buys, matching
  golden_cross_bullish, omit if null) --prices-json "<quotes>"`, then on
  approval, `python -m robinhood_bot.cli backtest record-fill {buy|sell}
  SYMBOL --run RUN_ID --asof <simulated date> --qty <n> --price <quote
  price> --sector <same sector, for buys> --rsi <same rsi, for buys>
  --ma-bullish/--no-ma-bullish (matching, for buys)
  --golden-cross-bullish/--no-golden-cross-bullish (matching, for buys)
  --reason "<why>"`. There is no live-order-placement call in this
  mode, ever.
```

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md
git commit -m "docs: document the golden-cross signal in the daily trading cycle skill"
```

---

## Final Verification

After all 7 tasks are complete:

```bash
python -m pytest tests/ -v
```

Expected: all tests pass, zero failures, zero errors.
