# Weekly Surplus Bonus Slots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A week's realized profit beyond that week's `weekly_profit_goal` becomes extra active-position slot capacity for the following week (capped at `max_bonus_active_slots`), so a hot week rewards the bot with modestly more trading capacity rather than just more same-week profit-taking.

**Architecture:** `PortfolioState` gains `prior_week_realized_pnl`, captured by `roll_week_if_needed` at every week boundary. A new pure function `bonus_active_slots(prior_week_realized_pnl, cfg) -> int` computes the bonus from that value. `evaluate_buy`'s existing hard slot-count check becomes an effective cap (`max_active_positions + bonus_active_slots(...)`) computed internally from `state` and `cfg` it already receives — no new parameters anywhere. The only other place with a duplicate slot-count calculation, `cmd_backtest_run`'s entries-loop optimization, is updated to match. `cmd_state` surfaces both the raw prior-week number and the computed effective cap so the LLM-driven trading skill can see real capacity instead of a hardcoded "5".

**Tech Stack:** Python 3.11+, pytest, existing `robinhood_bot` package conventions.

## Global Constraints

- `max_bonus_active_slots` default is `2`, defined on `RiskConfig` — no runtime CLI flag (matches every other risk threshold in this project).
- Bonus slots are computed fresh each week from ONLY the immediately preceding week's surplus — no multi-week stacking, no accumulation, no decay, no clawback. A losing or break-even prior week yields exactly `0` bonus slots.
- The bonus rate reuses `weekly_profit_goal` itself as the dollar unit — no separate "dollars per bonus slot" config field.
- This feature only ever changes active-slot *count* — position sizing (`max_position_pct`, `min_position_pct`, `long_hold_capital_cap_pct`) is completely untouched.
- No change to `current_weekly_tier`/`evaluate_profit_exits` (the existing within-week profit-taking escalation) — this is a separate, additive mechanism.
- Full test suite must stay green after every task: run `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v` before each commit.

---

### Task 1: `PortfolioState.prior_week_realized_pnl` + ledger persistence

**Files:**
- Modify: `robinhood_bot/portfolio_state.py`
- Modify: `robinhood_bot/ledger.py`
- Test: `tests/test_portfolio_state.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces: `PortfolioState.prior_week_realized_pnl: float = 0.0`; `roll_week_if_needed` now also captures the outgoing week's `week_realized_pnl` into `prior_week_realized_pnl` on a week rollover.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_portfolio_state.py`:

```python
def test_roll_week_if_needed_captures_prior_week_realized_pnl_on_rollover():
    state = PortfolioState(cash=10_000.0, week="2026-W01", week_realized_pnl=700.0)
    roll_week_if_needed(state, today=date(2026, 1, 12))
    assert state.prior_week_realized_pnl == 700.0
    assert state.week_realized_pnl == 0.0


def test_roll_week_if_needed_leaves_prior_week_realized_pnl_untouched_within_same_week():
    state = PortfolioState(
        cash=10_000.0, week="2026-W03", week_realized_pnl=250.0, prior_week_realized_pnl=700.0,
    )
    roll_week_if_needed(state, today=date(2026, 1, 15))
    assert state.prior_week_realized_pnl == 700.0
```

Append to `tests/test_ledger.py`:

```python
def test_save_and_load_round_trip_preserves_prior_week_realized_pnl(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(cash=8_000.0, prior_week_realized_pnl=1_200.0)
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.prior_week_realized_pnl == 1_200.0


def test_load_state_defaults_missing_prior_week_realized_pnl_to_zero_for_old_ledger_files(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "cash": 5_000.0, "active_positions": [], "long_hold_positions": [],
        "month": "", "month_start_equity": 0.0, "week": "", "week_realized_pnl": 0.0,
    }))

    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.prior_week_realized_pnl == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_portfolio_state.py tests/test_ledger.py -v`
Expected: FAIL — `TypeError: PortfolioState.__init__() got an unexpected keyword argument 'prior_week_realized_pnl'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/portfolio_state.py`, change the `PortfolioState` dataclass from:

```python
@dataclass
class PortfolioState:
    cash: float
    active_positions: list[Position] = field(default_factory=list)
    long_hold_positions: list[Position] = field(default_factory=list)
    month: str = ""
    month_start_equity: float = 0.0
    week: str = ""
    week_realized_pnl: float = 0.0
```

to:

```python
@dataclass
class PortfolioState:
    cash: float
    active_positions: list[Position] = field(default_factory=list)
    long_hold_positions: list[Position] = field(default_factory=list)
    month: str = ""
    month_start_equity: float = 0.0
    week: str = ""
    week_realized_pnl: float = 0.0
    prior_week_realized_pnl: float = 0.0
```

Then change `roll_week_if_needed` from:

```python
def roll_week_if_needed(state: PortfolioState, today: date) -> PortfolioState:
    iso_year, iso_week, _ = today.isocalendar()
    current_week = f"{iso_year:04d}-W{iso_week:02d}"
    if state.week != current_week:
        state.week = current_week
        state.week_realized_pnl = 0.0
    return state
```

to:

```python
def roll_week_if_needed(state: PortfolioState, today: date) -> PortfolioState:
    iso_year, iso_week, _ = today.isocalendar()
    current_week = f"{iso_year:04d}-W{iso_week:02d}"
    if state.week != current_week:
        state.prior_week_realized_pnl = state.week_realized_pnl
        state.week = current_week
        state.week_realized_pnl = 0.0
    return state
```

In `robinhood_bot/ledger.py`, change `state_to_dict` from:

```python
def state_to_dict(state: PortfolioState) -> dict:
    return {
        "cash": state.cash,
        "active_positions": [_position_to_dict(p) for p in state.active_positions],
        "long_hold_positions": [_position_to_dict(p) for p in state.long_hold_positions],
        "month": state.month,
        "month_start_equity": state.month_start_equity,
        "week": state.week,
        "week_realized_pnl": state.week_realized_pnl,
    }
```

to:

```python
def state_to_dict(state: PortfolioState) -> dict:
    return {
        "cash": state.cash,
        "active_positions": [_position_to_dict(p) for p in state.active_positions],
        "long_hold_positions": [_position_to_dict(p) for p in state.long_hold_positions],
        "month": state.month,
        "month_start_equity": state.month_start_equity,
        "week": state.week,
        "week_realized_pnl": state.week_realized_pnl,
        "prior_week_realized_pnl": state.prior_week_realized_pnl,
    }
```

And change `state_from_dict` from:

```python
def state_from_dict(data: dict) -> PortfolioState:
    return PortfolioState(
        cash=data["cash"],
        active_positions=[_position_from_dict(p) for p in data["active_positions"]],
        long_hold_positions=[_position_from_dict(p) for p in data["long_hold_positions"]],
        month=data.get("month", ""),
        month_start_equity=data.get("month_start_equity", 0.0),
        week=data.get("week", ""),
        week_realized_pnl=data.get("week_realized_pnl", 0.0),
    )
```

to:

```python
def state_from_dict(data: dict) -> PortfolioState:
    return PortfolioState(
        cash=data["cash"],
        active_positions=[_position_from_dict(p) for p in data["active_positions"]],
        long_hold_positions=[_position_from_dict(p) for p in data["long_hold_positions"]],
        month=data.get("month", ""),
        month_start_equity=data.get("month_start_equity", 0.0),
        week=data.get("week", ""),
        week_realized_pnl=data.get("week_realized_pnl", 0.0),
        prior_week_realized_pnl=data.get("prior_week_realized_pnl", 0.0),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_portfolio_state.py tests/test_ledger.py -v`
Expected: PASS (all existing + 4 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions (purely additive, defaulted field).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/portfolio_state.py robinhood_bot/ledger.py tests/test_portfolio_state.py tests/test_ledger.py
git commit -m "feat: capture and persist the prior week's realized P&L"
```

---

### Task 2: `bonus_active_slots` + `evaluate_buy` effective slot cap

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `PortfolioState.prior_week_realized_pnl` (Task 1).
- Produces: `RiskConfig.max_bonus_active_slots: int = 2`; `bonus_active_slots(prior_week_realized_pnl: float, cfg: RiskConfig) -> int`; `evaluate_buy`'s active-slot check now uses an effective cap instead of the raw `cfg.max_active_positions` (no signature change to `evaluate_buy` — it already receives `state` and `cfg`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_engine.py`, directly after `test_circuit_breaker_ignored_when_month_start_equity_zero` (before the `test_evaluate_buy_*` tests):

```python
def test_bonus_active_slots_zero_when_surplus_not_positive():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(500.0, cfg) == 0
    assert bonus_active_slots(0.0, cfg) == 0
    assert bonus_active_slots(-200.0, cfg) == 0


def test_bonus_active_slots_grants_one_slot_at_exact_surplus_boundary():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(1_000.0, cfg) == 1


def test_bonus_active_slots_grants_multiple_slots_for_larger_surplus():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(1_200.0, cfg) == 1
    assert bonus_active_slots(1_700.0, cfg) == 2


def test_bonus_active_slots_caps_at_max_bonus_active_slots():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(5_000.0, cfg) == 2
```

Update the import line near the top of `tests/test_risk_engine.py` from:

```python
from robinhood_bot.risk_engine import (
    RiskConfig, ExitAction, current_weekly_tier, evaluate_position, evaluate_profit_exits,
    max_new_position_value, circuit_breaker_tripped, evaluate_buy, evaluate_sell,
)
```

to:

```python
from robinhood_bot.risk_engine import (
    RiskConfig, ExitAction, bonus_active_slots, current_weekly_tier, evaluate_position,
    evaluate_profit_exits, max_new_position_value, circuit_breaker_tripped, evaluate_buy,
    evaluate_sell,
)
```

Append these two tests directly after `test_evaluate_buy_approves_when_sector_none_bypasses_concentration_check` (the last existing `evaluate_buy` test):

```python
def test_evaluate_buy_approves_when_bonus_slot_from_prior_week_surplus_allows_it():
    cfg = RiskConfig(
        max_active_positions=1, weekly_profit_goal=500.0, max_bonus_active_slots=2,
        max_position_pct=0.20,
    )
    state = PortfolioState(
        cash=10_000.0, month_start_equity=10_000.0, prior_week_realized_pnl=1_200.0,
        active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
        ],
    )
    decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials")
    assert decision.approved is True


def test_evaluate_buy_rejects_when_even_boosted_effective_cap_is_reached():
    cfg = RiskConfig(
        max_active_positions=1, weekly_profit_goal=500.0, max_bonus_active_slots=2,
        max_position_pct=0.20,
    )
    state = PortfolioState(
        cash=10_000.0, month_start_equity=10_000.0, prior_week_realized_pnl=1_200.0,
        active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology"),
            Position("MSFT", 5, 300.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Financials"),
        ],
    )
    decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Energy")
    assert decision.approved is False
    assert "no active slots available" in decision.reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'bonus_active_slots'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/risk_engine.py`, change `RiskConfig` from:

```python
@dataclass
class RiskConfig:
    max_active_positions: int = 5
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
```

Then, directly after the `current_weekly_tier` function, add:

```python
def bonus_active_slots(prior_week_realized_pnl: float, cfg: RiskConfig) -> int:
    surplus = prior_week_realized_pnl - cfg.weekly_profit_goal
    if surplus <= 0:
        return 0
    return min(cfg.max_bonus_active_slots, int(surplus // cfg.weekly_profit_goal))
```

Then change `evaluate_buy`'s slot-count check from:

```python
    if state.active_slot_count() >= cfg.max_active_positions:
        return BuyDecision(False, "no active slots available", max_value)
```

to:

```python
    effective_max_active_positions = cfg.max_active_positions + bonus_active_slots(
        state.prior_week_realized_pnl, cfg
    )
    if state.active_slot_count() >= effective_max_active_positions:
        return BuyDecision(False, "no active slots available", max_value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: PASS (all existing + 6 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions — every existing `PortfolioState`/`evaluate_buy` call either doesn't set `prior_week_realized_pnl` (defaults to `0.0`, giving `bonus_active_slots(0.0, cfg) == 0`, i.e. no behavior change) or is one of the two new tests above.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: grant bonus active-position slots from prior-week profit surplus"
```

---

### Task 3: `cmd_backtest_run` entries-loop effective cap + integration test

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Test: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `bonus_active_slots` (Task 2).
- Produces: no signature changes — `cmd_backtest_run`'s internal `free_slots` calculation now matches `evaluate_buy`'s effective cap.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_backtest_commands.py`, directly after `test_cmd_backtest_run_skips_same_sector_candidate_for_next_ranked` (before `test_cmd_backtest_report_computes_return_and_benchmark`):

```python
def test_cmd_backtest_run_fills_bonus_slot_from_prior_week_surplus(tmp_path):
    # max_active_positions=1 alone would allow only ONE of these two candidates
    # to be bought. prior_week_realized_pnl=1,200 with the default $500 goal
    # grants exactly 1 bonus slot (surplus $700 -> 1), so the effective cap is
    # 2 -- if the entries loop's free_slots calculation weren't updated to use
    # the same effective cap as evaluate_buy, only one symbol would get bought
    # here instead of both.
    bars = {
        "SPY": [HistoricalBar(date(2026, 1, 5), 400.0, 401.0, 399.0, 400.0)],
        "AAPL2": [
            HistoricalBar(date(2025, 12, 30), 98.0, 99.5, 97.5, 99.0),
            HistoricalBar(date(2025, 12, 31), 99.0, 100.5, 98.5, 100.0),
            HistoricalBar(date(2026, 1, 5), 100.0, 101.0, 99.0, 100.5),
        ],
        "JPM": [
            HistoricalBar(date(2025, 12, 30), 148.0, 149.5, 147.5, 149.0),
            HistoricalBar(date(2025, 12, 31), 149.0, 150.5, 148.5, 150.0),
            HistoricalBar(date(2026, 1, 5), 150.0, 151.0, 149.0, 150.5),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars.get(symbol, []) if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, weekly_profit_goal=500.0, max_bonus_active_slots=2,
        max_position_pct=0.5, min_position_pct=0.5, stop_loss_pct=0.5, grace_period_days=5,
    )

    # `week` is seeded to match the single trading day's own ISO week
    # (2026-01-05 is ISO week "2026-W02") so `roll_week_if_needed` sees no
    # week transition on this run and leaves the seeded
    # `prior_week_realized_pnl` untouched -- a transition would otherwise
    # overwrite it with the seeded `week_realized_pnl` (0.0).
    paths = backtest_commands.resolve_run_paths("run_bonus", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=10_000.0,
        week="2026-W02",
        prior_week_realized_pnl=1_200.0,
        month="2026-01",
        month_start_equity=10_000.0,
    ))

    backtest_commands.cmd_backtest_run(
        "run_bonus", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 5), end=date(2026, 1, 5),
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={}, store=store, cfg=cfg,
        vol_window_days=2, atr_window_days=2,
    )

    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    symbols = {p.symbol for p in final_state.active_positions}
    assert symbols == {"AAPL2", "JPM"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py::test_cmd_backtest_run_fills_bonus_slot_from_prior_week_surplus -v`
Expected: FAIL — with `max_active_positions=1` and the unfixed `free_slots = cfg.max_active_positions - state.active_slot_count()`, only 1 of the 2 candidates gets bought, so `symbols` will be a single-element set, not `{"AAPL2", "JPM"}`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/backtest_commands.py`, update the import line from:

```python
from .risk_engine import (
    ExitAction, RiskConfig, evaluate_buy, evaluate_position, evaluate_profit_exits,
    max_new_position_value,
)
```

to:

```python
from .risk_engine import (
    ExitAction, RiskConfig, bonus_active_slots, evaluate_buy, evaluate_position,
    evaluate_profit_exits, max_new_position_value,
)
```

Then change the "3. Entries" phase's `free_slots` line from:

```python
        # 3. Entries: fill free slots with the top-ranked candidate not already held.
        free_slots = cfg.max_active_positions - state.active_slot_count()
```

to:

```python
        # 3. Entries: fill free slots with the top-ranked candidate not already held.
        # Must match evaluate_buy's own effective-cap check (base cap + any
        # bonus slots earned from last week's profit surplus), or a bonus
        # week would be silently under-filled here even though evaluate_buy
        # itself would have approved the extra buy.
        effective_max_active_positions = cfg.max_active_positions + bonus_active_slots(
            state.prior_week_realized_pnl, cfg
        )
        free_slots = effective_max_active_positions - state.active_slot_count()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (all existing + 1 new test)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions (every existing `cmd_backtest_run` test seeds `prior_week_realized_pnl` implicitly as `0.0`, giving `bonus_active_slots(0.0, cfg) == 0` and thus an unchanged `free_slots` value).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: use the bonus-slot-aware effective cap in the backtest entries loop"
```

---

### Task 4: `cmd_state` visibility fields

**Files:**
- Modify: `robinhood_bot/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `bonus_active_slots` (Task 2); `PortfolioState.prior_week_realized_pnl` (Task 1).
- Produces: `cmd_state`'s output dict gains `"prior_week_realized_pnl"` and `"effective_max_active_positions"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_commands.py`, directly after `test_cmd_state_includes_trading_mode`:

```python
def test_cmd_state_includes_effective_max_active_positions_with_bonus(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, prior_week_realized_pnl=1_200.0))
    cfg = RiskConfig(max_active_positions=5, weekly_profit_goal=500.0, max_bonus_active_slots=2)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper",
        cfg=cfg,
    )

    assert result["prior_week_realized_pnl"] == 1_200.0
    assert result["effective_max_active_positions"] == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py::test_cmd_state_includes_effective_max_active_positions_with_bonus -v`
Expected: FAIL — `KeyError: 'prior_week_realized_pnl'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/commands.py`, update the import line from:

```python
from .risk_engine import (
    RiskConfig, ExitAction, current_weekly_tier, evaluate_buy, evaluate_position,
    evaluate_profit_exits, evaluate_sell,
)
```

to:

```python
from .risk_engine import (
    RiskConfig, ExitAction, bonus_active_slots, current_weekly_tier, evaluate_buy,
    evaluate_position, evaluate_profit_exits, evaluate_sell,
)
```

Then change `cmd_state`'s return dict from:

```python
    return {
        "trading_mode": trading_mode,
        "cash": state.cash,
        "active_positions": active_out,
        "long_hold_positions": long_hold_out,
        "total_equity": total_equity,
        "month": state.month,
        "month_start_equity": state.month_start_equity,
        "monthly_return_pct": (
            (total_equity / state.month_start_equity - 1.0)
            if state.month_start_equity > 0
            else 0.0
        ),
        "week": state.week,
        "week_realized_pnl": state.week_realized_pnl,
        "week_profit_target": current_weekly_tier(state.week_realized_pnl, cfg),
    }
```

to:

```python
    return {
        "trading_mode": trading_mode,
        "cash": state.cash,
        "active_positions": active_out,
        "long_hold_positions": long_hold_out,
        "total_equity": total_equity,
        "month": state.month,
        "month_start_equity": state.month_start_equity,
        "monthly_return_pct": (
            (total_equity / state.month_start_equity - 1.0)
            if state.month_start_equity > 0
            else 0.0
        ),
        "week": state.week,
        "week_realized_pnl": state.week_realized_pnl,
        "week_profit_target": current_weekly_tier(state.week_realized_pnl, cfg),
        "prior_week_realized_pnl": state.prior_week_realized_pnl,
        "effective_max_active_positions": cfg.max_active_positions + bonus_active_slots(
            state.prior_week_realized_pnl, cfg
        ),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: PASS (all existing + 1 new test)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions — this only adds two new keys to a dict; no existing test asserts an exhaustive/exact dict equality against `cmd_state`'s full return value (each existing test checks specific keys it cares about).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: surface prior-week P&L and effective active-slot cap in cmd_state"
```

---

### Task 5: `SKILL.md` documentation updates

**Files:**
- Modify: `.claude/skills/robinhood-trading/SKILL.md`

**Interfaces:**
- Consumes: nothing new — documents behavior already implemented in Tasks 1-4.
- Produces: nothing code-facing; no tests (matches this repo's existing precedent for doc-only SKILL.md updates).

- [ ] **Step 1: Update Step 1 (read mode & holdings) to mention prior-week P&L**

In `.claude/skills/robinhood-trading/SKILL.md`, change:

```
- Current `week_realized_pnl` and `week_profit_target`, for context on
  how much room is left before this week's profit goal — useful when
  weighing whether to cut a lagging position in Step 6 below.
```

to:

```
- Current `week_realized_pnl` and `week_profit_target`, for context on
  how much room is left before this week's profit goal — useful when
  weighing whether to cut a lagging position in Step 6 below.
- `prior_week_realized_pnl` and `effective_max_active_positions` — a
  strong prior week can raise this cycle's active-slot cap above the
  usual 5 (see Step 6). `effective_max_active_positions` is the real
  cap to use everywhere below; the "5-slot" figure from before this
  mechanism existed is only the baseline, not a hard ceiling anymore.
```

- [ ] **Step 2: Update Step 6 (research and decide) to use the effective cap**

Change:

```
- You can open at most as many new positions as there are free slots
  out of the 5-slot active cap (`5 - len(active_positions)` from
  Step 5's `active_positions`, since `WAITING` positions still occupy a
  slot).
```

to:

```
- You can open at most as many new positions as there are free slots
  out of the active cap (`effective_max_active_positions -
  len(active_positions)` from Step 1/Step 5's `state` output, since
  `WAITING` positions still occupy a slot). This cap may be higher than
  the usual 5 if last week cleared its profit goal with room to spare.
```

- [ ] **Step 3: Verify by reading the file back**

Re-read `.claude/skills/robinhood-trading/SKILL.md` in full and confirm both edits landed cleanly and nothing else changed.

- [ ] **Step 4: Run the full suite one more time**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (doc-only change, no test impact — final confirmation before finishing the branch).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md
git commit -m "docs: document the weekly surplus bonus-slot mechanism in the trading skill"
```
