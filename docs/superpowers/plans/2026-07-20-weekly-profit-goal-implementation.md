# Weekly Dollar Profit Goal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-position 8% profit target with a portfolio-level, dollar-denominated weekly profit goal (default $500) that sells the biggest winners first — across active, waiting, and long-hold positions alike — until each week's current tier is cleared, escalating the tier rather than shutting off profit-taking once the goal is first hit.

**Architecture:** A new pure function `risk_engine.evaluate_profit_exits` replaces `evaluate_position`'s old profit-target branch; `evaluate_position` now only ever decides the stop-loss/grace-period state machine (`HOLD`/`PROMOTE_LONG_HOLD`). A new `week`/`week_realized_pnl` pair on `PortfolioState` (rolled via `roll_week_if_needed`, mirroring the existing month-rolling pattern) tracks progress toward the current tier; `commands.cmd_record_fill` updates it on every sell. `commands.cmd_check_stop_losses` (live/paper, report-only) and `backtest_commands.cmd_backtest_run` (deterministic, self-executing) both gain a profit-taking phase that calls the new function; `commands.cmd_state` surfaces `week_realized_pnl`/`week_profit_target` for loss-sweep judgment calls.

**Tech Stack:** Python 3.11+, `pytest`. No new dependencies.

## Global Constraints

- `stop_loss_pct`, `grace_period_days`, `max_position_pct`/`min_position_pct`/`long_hold_capital_cap_pct`, and `monthly_circuit_breaker_pct` are all unchanged — only the profit-taking mechanism changes.
- `evaluate_position` never returns `SELL` again after this plan — it only ever returns `HOLD` or `PROMOTE_LONG_HOLD`. All profit-side `SELL` decisions come exclusively from the new `evaluate_profit_exits`.
- `evaluate_profit_exits` takes `positions: list[Position]` generically — callers always pass `active_positions + long_hold_positions` combined; a recovered long-hold position is exactly as eligible as an active winner.
- A missing quote for any profit-exit candidate excludes that candidate from consideration this call — never fabricated.
- `commands.cmd_check_stop_losses` keeps its existing report-only contract for `SELL`: it has never executed a sell itself, and that does not change here — only `backtest_commands.cmd_backtest_run` executes sells directly, since it has no human/MCP step in the way.
- `week_realized_pnl` is only ever mutated by `commands.cmd_record_fill`'s sell branch — `evaluate_profit_exits` itself is a pure function with no side effects.
- Every task ends green (`pytest` passing) before moving to the next. Current baseline: 126 tests passing (`test_risk_engine.py`: 22, `test_portfolio_state.py`: 7, `test_ledger.py`: 3, `test_commands.py`: 17, `test_backtest_commands.py`: 18, `test_cli.py`: 10, plus 49 untouched tests in `test_universe.py`/`test_moving_average.py`). Expected final total: 144.
- `python -m robinhood_bot.cli ...` and `pytest` both run via `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe` (this worktree has no `.venv` of its own — shared from the main checkout).

---

### Task 1: Weekly tracking fields on `PortfolioState` + ledger persistence

**Files:**
- Modify: `robinhood_bot/portfolio_state.py`
- Modify: `robinhood_bot/ledger.py`
- Test: `tests/test_portfolio_state.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces: `PortfolioState.week: str = ""`; `PortfolioState.week_realized_pnl: float = 0.0`; `roll_week_if_needed(state: PortfolioState, today: date) -> PortfolioState`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_portfolio_state.py`:

```python
def test_roll_week_if_needed_updates_on_new_week():
    state = PortfolioState(cash=10_000.0, week="2026-W01", week_realized_pnl=250.0)
    roll_week_if_needed(state, today=date(2026, 1, 12))
    assert state.week == "2026-W03"
    assert state.week_realized_pnl == 0.0


def test_roll_week_if_needed_no_change_within_same_week():
    state = PortfolioState(cash=10_000.0, week="2026-W03", week_realized_pnl=250.0)
    roll_week_if_needed(state, today=date(2026, 1, 15))
    assert state.week == "2026-W03"
    assert state.week_realized_pnl == 250.0


def test_roll_week_if_needed_handles_iso_year_boundary():
    state = PortfolioState(cash=10_000.0, week="2025-W52", week_realized_pnl=100.0)
    roll_week_if_needed(state, today=date(2025, 12, 29))
    assert state.week == "2026-W01"
    assert state.week_realized_pnl == 0.0
```

Update the import line at the top of `tests/test_portfolio_state.py` from:

```python
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState, roll_month_if_needed
```

to:

```python
from robinhood_bot.portfolio_state import (
    Position, PositionStatus, PortfolioState, roll_month_if_needed, roll_week_if_needed,
)
```

Append to `tests/test_ledger.py`:

```python
def test_save_and_load_round_trip_preserves_week_tracking(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(cash=8_000.0, week="2026-W28", week_realized_pnl=350.0)
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.week == "2026-W28"
    assert loaded.week_realized_pnl == 350.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_portfolio_state.py tests/test_ledger.py -v`
Expected: FAIL — `test_portfolio_state.py` with `ImportError: cannot import name 'roll_week_if_needed'`; `test_ledger.py`'s new test fails on `loaded.week` (`AttributeError`) once the import error above is fixed first — fix `portfolio_state.py` before re-running to see the `ledger.py` gap.

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
```

Then append, after the existing `roll_month_if_needed` function:

```python
def roll_week_if_needed(state: PortfolioState, today: date) -> PortfolioState:
    iso_year, iso_week, _ = today.isocalendar()
    current_week = f"{iso_year:04d}-W{iso_week:02d}"
    if state.week != current_week:
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
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_portfolio_state.py tests/test_ledger.py -v`
Expected: PASS (10 tests in `test_portfolio_state.py`, 4 tests in `test_ledger.py`)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/portfolio_state.py robinhood_bot/ledger.py tests/test_portfolio_state.py tests/test_ledger.py
git commit -m "feat: add weekly realized-P&L tracking to PortfolioState"
```

---

### Task 2: `risk_engine` — `evaluate_profit_exits` replaces the profit-target branch

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `Position`, `PositionStatus` (existing).
- Produces: `RiskConfig.weekly_profit_goal: float = 500.0` (replaces `profit_target_pct`); `current_weekly_tier(week_realized_pnl: float, cfg: RiskConfig) -> float`; `evaluate_profit_exits(positions: list[Position], prices: dict[str, float], week_realized_pnl: float, cfg: RiskConfig) -> list[Position]`. `evaluate_position` no longer accepts/uses `profit_target_pct` and never returns `ExitAction.SELL`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_risk_engine.py`, delete this test entirely (it tests behavior this task removes):

```python
def test_profit_target_hit_triggers_sell():
    cfg = RiskConfig(profit_target_pct=0.08)
    position = _position(entry_price=100.0)
    result = evaluate_position(position, current_price=110.0, today=date(2026, 7, 10), cfg=cfg)
    assert result.action == ExitAction.SELL
```

In the same file, remove the now-nonexistent `profit_target_pct=0.08` kwarg from these two `RiskConfig(...)` calls (leave everything else in each test unchanged):

```python
def test_small_loss_within_stop_loss_stays_active():
    cfg = RiskConfig(stop_loss_pct=0.05, profit_target_pct=0.08)
```
becomes
```python
def test_small_loss_within_stop_loss_stays_active():
    cfg = RiskConfig(stop_loss_pct=0.05)
```

```python
def test_recovery_from_waiting_returns_to_active():
    cfg = RiskConfig(stop_loss_pct=0.05, profit_target_pct=0.08)
```
becomes
```python
def test_recovery_from_waiting_returns_to_active():
    cfg = RiskConfig(stop_loss_pct=0.05)
```

Update the import line at the top of `tests/test_risk_engine.py` from:

```python
from robinhood_bot.risk_engine import RiskConfig, ExitAction, evaluate_position, max_new_position_value, circuit_breaker_tripped, evaluate_buy, evaluate_sell
```

to:

```python
from robinhood_bot.risk_engine import (
    RiskConfig, ExitAction, current_weekly_tier, evaluate_position, evaluate_profit_exits,
    max_new_position_value, circuit_breaker_tripped, evaluate_buy, evaluate_sell,
)
```

Append these new tests to the end of `tests/test_risk_engine.py`:

```python
def test_current_weekly_tier_at_zero_realized():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert current_weekly_tier(0.0, cfg) == 500.0


def test_current_weekly_tier_escalates_past_first_goal():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert current_weekly_tier(520.0, cfg) == 1000.0


def test_current_weekly_tier_handles_negative_realized_pnl():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert current_weekly_tier(-200.0, cfg) == 0.0


def test_evaluate_profit_exits_sells_single_winner_reaching_tier():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={"AAPL": 160.0}, week_realized_pnl=0.0, cfg=cfg)
    assert result == [position]


def test_evaluate_profit_exits_sells_biggest_winners_first_until_tier_cleared():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    big = _position(symbol="BIG", qty=10, entry_price=100.0)
    medium = _position(symbol="MED", qty=10, entry_price=100.0)
    small = _position(symbol="SML", qty=10, entry_price=100.0)

    result = evaluate_profit_exits(
        [small, big, medium],
        prices={"BIG": 140.0, "MED": 120.0, "SML": 105.0},
        week_realized_pnl=0.0, cfg=cfg,
    )

    assert result == [big, medium]


def test_evaluate_profit_exits_escalates_tier_when_goal_already_banked():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={"AAPL": 150.0}, week_realized_pnl=520.0, cfg=cfg)
    assert result == [position]


def test_evaluate_profit_exits_sells_nothing_without_positive_gains():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={"AAPL": 95.0}, week_realized_pnl=0.0, cfg=cfg)
    assert result == []


def test_evaluate_profit_exits_skips_candidate_with_missing_quote():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={}, week_realized_pnl=0.0, cfg=cfg)
    assert result == []


def test_evaluate_profit_exits_treats_long_hold_positions_as_eligible():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    long_hold = _position(symbol="TSLA", qty=5, entry_price=200.0, status=PositionStatus.LONG_HOLD)
    result = evaluate_profit_exits([long_hold], prices={"TSLA": 320.0}, week_realized_pnl=0.0, cfg=cfg)
    assert result == [long_hold]
```

**Hand-verification for the biggest-first test:** gains are `BIG`=`(140-100)*10=400`, `MED`=`(120-100)*10=200`, `SML`=`(105-100)*10=50`. Sorted descending: `[(400, big), (200, med), (50, small)]`. `tier = (int(0.0 // 500.0) + 1) * 500.0 = 500.0`. Walking the sorted list: `running=0 < 500` → take `big`, `running=400`; `400 < 500` → take `med`, `running=600`; `600 >= 500` → stop before `small`. Result `[big, med]` — proves both the biggest-first ordering (via the input list being shuffled) and the "stop once cleared" behavior (small stays unsold even though it's profitable).

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'current_weekly_tier'`

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/risk_engine.py`, change `RiskConfig` from:

```python
@dataclass
class RiskConfig:
    max_active_positions: int = 5
    stop_loss_pct: float = 0.05
    profit_target_pct: float = 0.08
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
    stop_loss_pct: float = 0.05
    weekly_profit_goal: float = 500.0
    grace_period_days: int = 5
    max_position_pct: float = 0.20
    min_position_pct: float = 0.05
    long_hold_capital_cap_pct: float = 0.30
    monthly_circuit_breaker_pct: float = 0.10
```

Change `evaluate_position` from:

```python
def evaluate_position(
    position: Position, current_price: float, today: date, cfg: RiskConfig
) -> PositionEvaluation:
    pnl_pct = (current_price - position.entry_price) / position.entry_price

    if pnl_pct >= cfg.profit_target_pct:
        return PositionEvaluation(ExitAction.SELL, position.status, None)

    if pnl_pct <= -cfg.stop_loss_pct:
        underwater_since = position.underwater_since or today
        days_underwater = (today - underwater_since).days
        if days_underwater > cfg.grace_period_days:
            return PositionEvaluation(ExitAction.PROMOTE_LONG_HOLD, PositionStatus.LONG_HOLD, None)
        return PositionEvaluation(ExitAction.HOLD, PositionStatus.WAITING, underwater_since)

    return PositionEvaluation(ExitAction.HOLD, PositionStatus.ACTIVE, None)
```

to:

```python
def evaluate_position(
    position: Position, current_price: float, today: date, cfg: RiskConfig
) -> PositionEvaluation:
    pnl_pct = (current_price - position.entry_price) / position.entry_price

    if pnl_pct <= -cfg.stop_loss_pct:
        underwater_since = position.underwater_since or today
        days_underwater = (today - underwater_since).days
        if days_underwater > cfg.grace_period_days:
            return PositionEvaluation(ExitAction.PROMOTE_LONG_HOLD, PositionStatus.LONG_HOLD, None)
        return PositionEvaluation(ExitAction.HOLD, PositionStatus.WAITING, underwater_since)

    return PositionEvaluation(ExitAction.HOLD, PositionStatus.ACTIVE, None)
```

Then append, right after `evaluate_position`:

```python
def current_weekly_tier(week_realized_pnl: float, cfg: RiskConfig) -> float:
    return (int(week_realized_pnl // cfg.weekly_profit_goal) + 1) * cfg.weekly_profit_goal


def evaluate_profit_exits(
    positions: list[Position], prices: dict[str, float], week_realized_pnl: float, cfg: RiskConfig,
) -> list[Position]:
    gains = []
    for position in positions:
        price = prices.get(position.symbol)
        if price is None:
            continue
        gain = (price - position.entry_price) * position.qty
        if gain > 0:
            gains.append((gain, position))
    gains.sort(key=lambda g: g[0], reverse=True)

    tier = current_weekly_tier(week_realized_pnl, cfg)
    to_sell = []
    running = week_realized_pnl
    for gain, position in gains:
        if running >= tier:
            break
        to_sell.append(position)
        running += gain
    return to_sell
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: PASS (30 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: replace per-position profit target with weekly dollar profit goal"
```

---

### Task 3: `cmd_record_fill` accumulates weekly realized P&L

**Files:**
- Modify: `robinhood_bot/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `PortfolioState.week_realized_pnl` (Task 1).
- Produces: `cmd_record_fill`'s sell branch updates `state.week_realized_pnl` by `(price - position.entry_price) * position.qty` on every executed sell (gain or loss).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
def test_cmd_record_fill_sell_accumulates_week_realized_pnl(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        week="2026-W27", week_realized_pnl=50.0,
    ))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=110.0, today=date(2026, 7, 10), reason="profit target",
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.week_realized_pnl == pytest.approx(150.0)


def test_cmd_record_fill_sell_at_a_loss_decreases_week_realized_pnl(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        week="2026-W27", week_realized_pnl=200.0,
    ))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=90.0, today=date(2026, 7, 10), reason="stop loss",
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.week_realized_pnl == pytest.approx(100.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -k week_realized_pnl -v`
Expected: FAIL — both assert `0.0 == pytest.approx(150.0)` / `0.0 == pytest.approx(100.0)` since `week_realized_pnl` isn't updated yet.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/commands.py`, change the sell branch of `cmd_record_fill` from:

```python
    elif action == "sell":
        position = state.find_active(symbol) or state.find_long_hold(symbol)
        if position is None:
            raise ValueError(f"{symbol} not currently held")
        if qty != position.qty:
            raise ValueError(
                f"sell qty {qty} does not match held qty {position.qty} for {symbol} "
                "(partial sells are not supported)"
            )
        state.cash += position.qty * price
        if position in state.active_positions:
            state.active_positions.remove(position)
        else:
            state.long_hold_positions.remove(position)
```

to:

```python
    elif action == "sell":
        position = state.find_active(symbol) or state.find_long_hold(symbol)
        if position is None:
            raise ValueError(f"{symbol} not currently held")
        if qty != position.qty:
            raise ValueError(
                f"sell qty {qty} does not match held qty {position.qty} for {symbol} "
                "(partial sells are not supported)"
            )
        state.cash += position.qty * price
        state.week_realized_pnl += (price - position.entry_price) * position.qty
        if position in state.active_positions:
            state.active_positions.remove(position)
        else:
            state.long_hold_positions.remove(position)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: accumulate weekly realized P&L on every record-fill sell"
```

---

### Task 4: `cmd_state` surfaces weekly tracking and rolls the week

**Files:**
- Modify: `robinhood_bot/commands.py`
- Modify: `robinhood_bot/backtest_commands.py`
- Modify: `robinhood_bot/cli.py`
- Test: `tests/test_commands.py`
- Test: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `roll_week_if_needed` (Task 1); `current_weekly_tier` (Task 2).
- Produces: `cmd_state(ledger_path, starting_cash, prices, today, trading_mode, cfg: RiskConfig) -> dict` — gains a required `cfg` parameter and two new output keys, `week_realized_pnl: float` and `week_profit_target: float`, alongside the existing `week` key already implied by `PortfolioState`. `cmd_backtest_state(run_id, base_dir, starting_cash, prices, asof, cfg: RiskConfig) -> dict` — gains the same required `cfg` parameter, threaded straight through.

- [ ] **Step 1: Write the failing tests**

Update every existing `commands.cmd_state(...)` call in `tests/test_commands.py` to add `cfg=RiskConfig()` as the final keyword argument. For example, change:

```python
    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper",
    )
```

to:

```python
    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
    )
```

Apply this same `cfg=RiskConfig()` addition to the `cmd_state` calls in `test_cmd_state_marks_missing_price_as_stale`, `test_cmd_state_rolls_month_and_persists`, and `test_cmd_state_includes_trading_mode`.

Then append these new tests to `tests/test_commands.py`:

```python
def test_cmd_state_includes_week_tracking_fields(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, week="2026-W28", week_realized_pnl=250.0))
    cfg = RiskConfig(weekly_profit_goal=500.0)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper", cfg=cfg,
    )

    assert result["week"] == "2026-W28"
    assert result["week_realized_pnl"] == 250.0
    assert result["week_profit_target"] == 500.0


def test_cmd_state_rolls_week_and_persists(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, week="2026-W27", week_realized_pnl=250.0))
    cfg = RiskConfig()

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper", cfg=cfg,
    )

    assert result["week"] == "2026-W28"
    assert result["week_realized_pnl"] == 0.0

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.week == "2026-W28"
```

**Hand-verification:** 2026-07-10 is a Friday within ISO week 28 of 2026 (2026-07-06 Monday through 2026-07-12 Sunday) — this matches the actual trading-day range this session already fetched for `2026-07-06`..`2026-07-17` during the earlier backtests. `current_weekly_tier(250.0, RiskConfig(weekly_profit_goal=500.0))` = `(int(250.0 // 500.0) + 1) * 500.0` = `(0 + 1) * 500.0` = `500.0`, matching the first test's assertion.

In `tests/test_backtest_commands.py`, update the one existing `cmd_backtest_state` call, changing:

```python
    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5),
    )
```

to:

```python
    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5), cfg=RiskConfig(),
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py tests/test_backtest_commands.py -v`
Expected: FAIL with `TypeError: cmd_state() missing 1 required positional argument: 'cfg'` (and the same for `cmd_backtest_state`)

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/commands.py`, add `current_weekly_tier` to the risk_engine import — change:

```python
from .risk_engine import RiskConfig, ExitAction, evaluate_buy, evaluate_position, evaluate_sell
```

to:

```python
from .risk_engine import RiskConfig, ExitAction, current_weekly_tier, evaluate_buy, evaluate_position, evaluate_sell
```

Also add `roll_week_if_needed` to the portfolio_state import — change:

```python
from .portfolio_state import Position, PositionStatus, roll_month_if_needed
```

to:

```python
from .portfolio_state import Position, PositionStatus, roll_month_if_needed, roll_week_if_needed
```

Then change `cmd_state` from:

```python
def cmd_state(
    ledger_path: Path,
    starting_cash: float,
    prices: dict[str, float],
    today: date,
    trading_mode: str,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    active_out = [_position_summary(p, prices) for p in state.active_positions]
    long_hold_out = [_position_summary(p, prices) for p in state.long_hold_positions]
    positions_value = sum(o["current_value"] for o in active_out + long_hold_out)
    total_equity = state.cash + positions_value

    roll_month_if_needed(state, today, total_equity)
    ledger.save_state(ledger_path, state)

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
    }
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
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    active_out = [_position_summary(p, prices) for p in state.active_positions]
    long_hold_out = [_position_summary(p, prices) for p in state.long_hold_positions]
    positions_value = sum(o["current_value"] for o in active_out + long_hold_out)
    total_equity = state.cash + positions_value

    roll_month_if_needed(state, today, total_equity)
    roll_week_if_needed(state, today)
    ledger.save_state(ledger_path, state)

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

In `robinhood_bot/backtest_commands.py`, change `cmd_backtest_state` from:

```python
def cmd_backtest_state(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_state(paths.ledger, starting_cash, prices, asof, trading_mode="backtest")
```

to:

```python
def cmd_backtest_state(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
    cfg: RiskConfig,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_state(paths.ledger, starting_cash, prices, asof, trading_mode="backtest", cfg=cfg)
```

In `robinhood_bot/cli.py`, change the live `state` dispatch inside `main()` from:

```python
    if args.command == "state":
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE
        )
```

to:

```python
    if args.command == "state":
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE, cfg
        )
```

And change `_dispatch_backtest`'s `"state"` case from:

```python
    if args.backtest_command == "state":
        return backtest_commands.cmd_backtest_state(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof),
        )
```

to:

```python
    if args.backtest_command == "state":
        return backtest_commands.cmd_backtest_state(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof), cfg,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py tests/test_backtest_commands.py tests/test_cli.py -v`
Expected: PASS (`test_commands.py`: 21, `test_backtest_commands.py`: 18, `test_cli.py`: 10 — unchanged, since `cli.py`'s tests invoke through `cli.main(...)`, not `commands.cmd_state` directly)

- [ ] **Step 5: Run the full suite and commit**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (142 tests: 126 baseline + 16 net new from Tasks 1-4 — Task 1: +4, Task 2: +8 net (9 new − 1 removed), Task 3: +2, Task 4: +2)

```bash
git add robinhood_bot/commands.py robinhood_bot/backtest_commands.py robinhood_bot/cli.py tests/test_commands.py tests/test_backtest_commands.py
git commit -m "feat: surface weekly profit-goal progress from cmd_state"
```

---

### Task 5: `cmd_check_stop_losses` reports weekly profit-goal exits

**Files:**
- Modify: `robinhood_bot/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `evaluate_profit_exits` (Task 2).
- Produces: `cmd_check_stop_losses` gains a profit-exit reporting phase — for every position `evaluate_profit_exits` selects, appends `{"symbol": ..., "action": "SELL", "current_status": ..., "new_status": ...}` (same status in both fields, since this call never mutates a position for a profit exit) to `results`. Still report-only: no sell is ever executed by this function.

- [ ] **Step 1: Write the failing test**

In `tests/test_commands.py`, replace this existing test:

```python
def test_check_stop_losses_reports_sell_without_removing_position(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(profit_target_pct=0.08)

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10), cfg=cfg, apply=True,
    )

    assert result["results"][0]["action"] == "SELL"
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].symbol == "AAPL"
```

with:

```python
def test_check_stop_losses_reports_profit_exit_without_removing_position(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(weekly_profit_goal=500.0)

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"AAPL": 160.0}, today=date(2026, 7, 10), cfg=cfg, apply=True,
    )

    sell_results = [r for r in result["results"] if r["action"] == "SELL"]
    assert sell_results == [
        {"symbol": "AAPL", "action": "SELL", "current_status": "ACTIVE", "new_status": "ACTIVE"}
    ]
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].symbol == "AAPL"
```

Then append this new test:

```python
def test_check_stop_losses_reports_profit_exit_for_recovered_long_hold_position(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        long_hold_positions=[Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)],
    ))
    cfg = RiskConfig(weekly_profit_goal=500.0)

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"TSLA": 320.0}, today=date(2026, 7, 10), cfg=cfg, apply=True,
    )

    sell_results = [r for r in result["results"] if r["action"] == "SELL"]
    assert sell_results == [
        {"symbol": "TSLA", "action": "SELL", "current_status": "LONG_HOLD", "new_status": "LONG_HOLD"}
    ]
```

**Hand-verification:** AAPL gain = `(160-100)*10=600 >= tier(500)` → reported as a profit exit. TSLA gain = `(320-200)*5=600 >= tier(500)` → reported too, even though it's only ever iterated via `long_hold_positions`, never the per-position stop-loss loop (which only walks `active_positions`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: FAIL — `test_check_stop_losses_reports_profit_exit_without_removing_position` gets an empty `sell_results` (old `evaluate_position` no longer returns `SELL`, and there's no profit-exit phase yet); `test_check_stop_losses_reports_profit_exit_for_recovered_long_hold_position` fails the same way.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/commands.py`, add `evaluate_profit_exits` to the risk_engine import — change:

```python
from .risk_engine import RiskConfig, ExitAction, current_weekly_tier, evaluate_buy, evaluate_position, evaluate_sell
```

to:

```python
from .risk_engine import (
    RiskConfig, ExitAction, current_weekly_tier, evaluate_buy, evaluate_position,
    evaluate_profit_exits, evaluate_sell,
)
```

Then change `cmd_check_stop_losses` from:

```python
def cmd_check_stop_losses(
    ledger_path: Path,
    starting_cash: float,
    prices: dict[str, float],
    today: date,
    cfg: RiskConfig,
    apply: bool,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    results = []
    remaining_active = []

    for position in state.active_positions:
        price = prices.get(position.symbol)
        if price is None:
            results.append({"symbol": position.symbol, "action": "SKIP", "reason": "no fresh price"})
            remaining_active.append(position)
            continue

        evaluation = evaluate_position(position, price, today, cfg)
        results.append({
            "symbol": position.symbol,
            "action": evaluation.action.value,
            "current_status": position.status.value,
            "new_status": evaluation.new_status.value,
        })

        if not apply or evaluation.action == ExitAction.SELL:
            remaining_active.append(position)
            continue

        position.status = evaluation.new_status
        position.underwater_since = evaluation.new_underwater_since

        if evaluation.action == ExitAction.PROMOTE_LONG_HOLD:
            state.long_hold_positions.append(position)
        else:
            remaining_active.append(position)

    state.active_positions = remaining_active

    if apply:
        ledger.save_state(ledger_path, state)

    return {"results": results, "applied": apply}
```

to:

```python
def cmd_check_stop_losses(
    ledger_path: Path,
    starting_cash: float,
    prices: dict[str, float],
    today: date,
    cfg: RiskConfig,
    apply: bool,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    results = []
    remaining_active = []

    for position in state.active_positions:
        price = prices.get(position.symbol)
        if price is None:
            results.append({"symbol": position.symbol, "action": "SKIP", "reason": "no fresh price"})
            remaining_active.append(position)
            continue

        evaluation = evaluate_position(position, price, today, cfg)
        results.append({
            "symbol": position.symbol,
            "action": evaluation.action.value,
            "current_status": position.status.value,
            "new_status": evaluation.new_status.value,
        })

        if not apply:
            remaining_active.append(position)
            continue

        position.status = evaluation.new_status
        position.underwater_since = evaluation.new_underwater_since

        if evaluation.action == ExitAction.PROMOTE_LONG_HOLD:
            state.long_hold_positions.append(position)
        else:
            remaining_active.append(position)

    state.active_positions = remaining_active

    profit_exits = evaluate_profit_exits(
        state.active_positions + state.long_hold_positions, prices, state.week_realized_pnl, cfg,
    )
    for position in profit_exits:
        results.append({
            "symbol": position.symbol,
            "action": "SELL",
            "current_status": position.status.value,
            "new_status": position.status.value,
        })

    if apply:
        ledger.save_state(ledger_path, state)

    return {"results": results, "applied": apply}
```

Note the simplification: the old `if not apply or evaluation.action == ExitAction.SELL:` guard drops its second condition, since `evaluate_position` can no longer return `SELL` — that branch is now unreachable dead code being removed, not a behavior change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: PASS (22 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: report weekly profit-goal exits from cmd_check_stop_losses"
```

---

### Task 6: `cmd_backtest_run` executes weekly profit-goal exits

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Test: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `roll_week_if_needed` (Task 1); `evaluate_profit_exits` (Task 2).
- Produces: `cmd_backtest_run`'s daily loop gains a profit-taking phase between the stop-loss/promotion phase and the entries phase — it calls `evaluate_profit_exits` on `active_positions + long_hold_positions` and executes every returned sell directly via `commands.cmd_record_fill`, with reason `"weekly profit-goal exit"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_backtest_commands.py`, update the existing hand-verified test. Change:

```python
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.05, profit_target_pct=0.08,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
    )
```

to:

```python
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.05, weekly_profit_goal=200.0,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
    )
```

And add these two assertions right after the existing `assert final_state.month_start_equity == pytest.approx(10_000.0)` line:

```python
    assert final_state.week == "2026-W02"
    assert final_state.week_realized_pnl == pytest.approx(400.0)
```

Update the docstring-style comment above the test (the paragraph explaining the scenario) — change the sentence describing the trigger from:

```
Day 2 (2026-01-05), `A` closes at $108 — an 8% gain that exactly hits
`profit_target_pct`, triggering a `SELL`; the freed slot immediately
re-buys `A` at $108 (48 shares, `floor($5,200 / $108)`), since it's
still the only (and therefore top-ranked) candidate.
```

to:

```
Day 2 (2026-01-05), `A` closes at $108 — a $400 gain (`(108-100)*50`)
against a $200 `weekly_profit_goal`, so `evaluate_profit_exits` sells
the whole position; the freed slot immediately re-buys `A` at $108 (48
shares, `floor($5,200 / $108)`), since it's still the only (and
therefore top-ranked) candidate. `week_realized_pnl` ends the run at
$400 (the full realized gain, not capped at the $200 goal — the goal
only decides whether to sell, never how much of the gain to keep).
```

**Hand-verification (unchanged from the original, since none of the buy/sell/rebuy dollar amounts change — only the trigger mechanism does):** Day 1: `total_equity=10000`, `max_value=0.5*10000=5000`, buy 50 shares of `A` @ $100, `cash=5000`. Day 2: `evaluate_position` no longer sells at 8% — but `A`'s gain of `(108-100)*50=400` exceeds `tier=(int(0//200)+1)*200=200` (since `week_realized_pnl` starts at `0` for both days — day 1 has no sells, and day 2's own `roll_week_if_needed` call, evaluated before the profit-exit phase, resets it to `0` anyway since day 1 falls in ISO week `2026-W01` and day 2 falls in `2026-W02`), so `evaluate_profit_exits` sells all 50 shares @ $108, `cash=5000+50*108=10400`, `week_realized_pnl=0+400=400`. Entries phase then re-buys: `total_equity=10400`, `max_value=0.5*10400=5200`, `qty=floor(5200/108)=48`, `cash=10400-48*108=5216.00` — identical to every dollar figure the original test already asserted.

Append this new, focused test proving long-hold eligibility inside the deterministic loop:

```python
def test_cmd_backtest_run_sweeps_recovered_long_hold_position_for_profit(tmp_path):
    bars = {
        "A": [HistoricalBar(date(2026, 1, 2), 148.0, 151.0, 147.0, 150.0)],
        "SPY": [HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0)],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars[symbol] if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(max_active_positions=0, weekly_profit_goal=300.0)

    paths = backtest_commands.resolve_run_paths("run2", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=1_000.0,
        long_hold_positions=[Position("A", 10, 100.0, date(2025, 12, 1), PositionStatus.LONG_HOLD)],
    ))

    backtest_commands.cmd_backtest_run(
        "run2", tmp_path, starting_cash=1_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=[], store=store, cfg=cfg,
    )

    final_state = ledger.load_state(paths.ledger, starting_cash=1_000.0)
    assert final_state.long_hold_positions == []
    assert final_state.cash == pytest.approx(2_500.0)
    assert final_state.week_realized_pnl == pytest.approx(500.0)

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["action"] == "SELL"
    assert rows[0]["symbol"] == "A"
    assert rows[0]["reason"] == "weekly profit-goal exit"
```

Add the two new imports this test needs at the top of `tests/test_backtest_commands.py` (append to the existing `from robinhood_bot.portfolio_state import ...` line):

```python
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState
```

**Hand-verification:** `max_active_positions=0` means `free_slots = 0 - 0 = 0`, so the entries phase never runs (`candidate_symbols=[]` too, so `rank_candidates_as_of` is never even called). The only position is the seeded `LONG_HOLD` — `A`'s gain is `(150-100)*10=500 >= tier=(int(0//300)+1)*300=300`, so it's sold: `cash=1000+10*150=2500`, `week_realized_pnl=0+500=500`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: FAIL — the updated hand-verified test fails on the new `week`/`week_realized_pnl` assertions (fields don't exist as expected yet since the profit-exit phase isn't wired); the new long-hold test fails with `AssertionError` on `final_state.long_hold_positions == []` (nothing gets sold yet).

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/backtest_commands.py`, update the imports — change:

```python
from .portfolio_state import roll_month_if_needed
from .risk_engine import ExitAction, RiskConfig, evaluate_buy, evaluate_position, max_new_position_value
```

to:

```python
from .portfolio_state import roll_month_if_needed, roll_week_if_needed
from .risk_engine import (
    ExitAction, RiskConfig, evaluate_buy, evaluate_position, evaluate_profit_exits,
    max_new_position_value,
)
```

Then change `cmd_backtest_run`'s body from:

```python
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
```

to:

```python
    for today in trading_days:
        # 1. Exits: evaluate every active position's stop-loss/grace-period state.
        # `evaluate_position` never returns SELL — it only ever moves a position
        # between HOLD/WAITING/PROMOTE_LONG_HOLD, so this phase never touches
        # cash and never needs to call `cmd_record_fill` (unlike before this
        # phase absorbed the profit-target branch too).
        state = ledger.load_state(paths.ledger, starting_cash)
        remaining_active = []
        for position in state.active_positions:
            price = store.get_close(position.symbol, today)
            if price is None:
                remaining_active.append(position)
                continue
            evaluation = evaluate_position(position, price, today, cfg)
            position.status = evaluation.new_status
            position.underwater_since = evaluation.new_underwater_since
            if evaluation.action == ExitAction.PROMOTE_LONG_HOLD:
                state.long_hold_positions.append(position)
            else:
                remaining_active.append(position)
        state.active_positions = remaining_active
        ledger.save_state(paths.ledger, state)

        # Roll the monthly circuit-breaker baseline and the weekly profit-goal
        # tracker exactly like `cmd_state` does, since this loop never calls
        # `cmd_state` itself.
        state = ledger.load_state(paths.ledger, starting_cash)
        cash, positions_value = _total_equity(state, store, today)
        roll_month_if_needed(state, today, cash + positions_value)
        roll_week_if_needed(state, today)
        ledger.save_state(paths.ledger, state)

        # 2. Profit-taking: sell the biggest winners (active or long-hold) needed
        # to reach this week's current tier — see risk_engine.evaluate_profit_exits.
        state = ledger.load_state(paths.ledger, starting_cash)
        profit_candidates = state.active_positions + state.long_hold_positions
        profit_prices = {
            p.symbol: price
            for p in profit_candidates
            if (price := store.get_close(p.symbol, today)) is not None
        }
        for position in evaluate_profit_exits(profit_candidates, profit_prices, state.week_realized_pnl, cfg):
            commands.cmd_record_fill(
                paths.ledger, paths.trade_log, starting_cash, "sell", position.symbol,
                position.qty, profit_prices[position.symbol], today, "weekly profit-goal exit",
            )
            state = ledger.load_state(paths.ledger, starting_cash)

        # 3. Entries: fill free slots with the top-ranked candidate not already held.
        free_slots = cfg.max_active_positions - state.active_slot_count()
```

Leave the rest of the function (the entries phase and the equity-curve append at the end of the loop) exactly as it is — the entries phase already reloads `state` from disk before computing `free_slots`, so it automatically reflects any profit-taking sells from the new phase 2.

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (144 tests total)

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: execute weekly profit-goal exits in the deterministic backtest loop"
```

---

### Task 7: Update `SKILL.md` docs for both trading skills

**Files:**
- Modify: `.claude/skills/robinhood-trading/SKILL.md`
- Modify: `.claude/skills/robinhood-stop-loss-sweep/SKILL.md`

**Interfaces:**
- Consumes: `week_realized_pnl`/`week_profit_target` (Task 4, surfaced via `cli.py state`/`backtest state`); the profit-exit `SELL` entries now reported by `cli.py check-stop-losses`/`backtest check-stop-losses` (Task 5).
- Produces: no code — documentation only, no automated test applies (matches this project's existing convention that `SKILL.md` content is verified by actually running the skill, not by `pytest`).

- [ ] **Step 1: Update `robinhood-stop-loss-sweep/SKILL.md`**

Change Step 1 from:

```markdown
## Step 1 — Read current holdings

```
python -m robinhood_bot.cli state --prices-json "{}"
```

Note `trading_mode` and every symbol in `active_positions` (this
includes both `ACTIVE` and `WAITING` status positions — both occupy an
active slot and both are checked here).
```

to:

```markdown
## Step 1 — Read current holdings

```
python -m robinhood_bot.cli state --prices-json "{}"
```

Note `trading_mode` and every symbol in `active_positions` (this
includes both `ACTIVE` and `WAITING` status positions — both occupy an
active slot and both are checked here) **and** every symbol in
`long_hold_positions` — a recovered long-hold position is now eligible
for the weekly profit-goal sweep below, exactly like an active winner.
```

Change Step 2 from:

```markdown
## Step 2 — Get fresh quotes

Using the Robinhood MCP quote tool (e.g. `get_equity_quotes`), fetch a
current price for every symbol from Step 1's `active_positions`.

**If a quote fails for any symbol: skip that symbol this sweep.** Never
fabricate or reuse a stale price.
```

to:

```markdown
## Step 2 — Get fresh quotes

Using the Robinhood MCP quote tool (e.g. `get_equity_quotes`), fetch a
current price for every symbol from Step 1's `active_positions` **and**
`long_hold_positions`.

**If a quote fails for any symbol: skip that symbol this sweep.** Never
fabricate or reuse a stale price.
```

Change Step 3's prose (leave the command itself unchanged) from:

```markdown
This one command evaluates every active position against its
stop-loss/profit-target thresholds and the long-hold grace period, and
returns a `results` list. Because `--apply` was passed, any
`PROMOTE_LONG_HOLD` result has **already been applied** to the ledger —
the position has moved from `active_positions` to `long_hold_positions`.
You don't need to do anything further for those.
```

to:

```markdown
This one command evaluates every active position's stop-loss/grace-
period state, **and** checks all active *and* long-hold positions
against this week's profit goal (`week_realized_pnl` vs.
`week_profit_target` from `state`) — the biggest winners get flagged for
sale first, escalating to the next tier once the current one is
cleared, rather than stopping for the rest of the week the moment the
goal is first hit. Returns a `results` list. Because `--apply` was
passed, any `PROMOTE_LONG_HOLD` result has **already been applied** to
the ledger — the position has moved from `active_positions` to
`long_hold_positions`. You don't need to do anything further for those.
```

Change Step 4 from:

```markdown
## Step 4 — Execute any SELL results

For each entry in `results` where `"action": "SELL"`:

**If `trading_mode` is `"paper"`:**

```
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote from Step 2> --reason "stop-loss sweep: profit target hit"
```

`<held qty>` isn't in the `check-stop-losses` result — pull it from
Step 1's `active_positions` data for this symbol.
```

to:

```markdown
## Step 4 — Execute any SELL results

For each entry in `results` where `"action": "SELL"`:

**If `trading_mode` is `"paper"`:**

```
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote from Step 2> --reason "weekly profit-goal exit"
```

`<held qty>` isn't in the `check-stop-losses` result — pull it from
Step 1's `active_positions` **or** `long_hold_positions` data for this
symbol, since a sold symbol could now be either.
```

- [ ] **Step 2: Update `robinhood-trading/SKILL.md`**

Change Step 1's bullet list from:

```markdown
- `trading_mode`: `"paper"` or `"live"`. **This governs everything below.**
  Never call the live order-placement MCP tool while this is `"paper"`.
- The symbols currently in `active_positions` and `long_hold_positions`.
- Current `month_start_equity` and `monthly_return_pct`, for context on
  progress toward this month's return goal.
```

to:

```markdown
- `trading_mode`: `"paper"` or `"live"`. **This governs everything below.**
  Never call the live order-placement MCP tool while this is `"paper"`.
- The symbols currently in `active_positions` and `long_hold_positions`.
- Current `month_start_equity` and `monthly_return_pct`, for context on
  progress toward this month's return goal.
- Current `week_realized_pnl` and `week_profit_target`, for context on
  how much room is left before this week's profit goal — useful when
  weighing whether to cut a lagging position in Step 6 below.
```

Change Step 6 from:

```markdown
## Step 6 — Research and decide, per shortlisted symbol

For each symbol currently **held** (active or long-hold):
- Note its lifecycle `status` (`ACTIVE`, `WAITING`, `LONG_HOLD`) and
  `unrealized_pnl_pct` from Step 5.
- `LONG_HOLD` positions are not part of today's short-term rotation —
  only consider selling one if it has clearly recovered and you'd
  exit it; otherwise leave it alone.
- For `ACTIVE`/`WAITING` positions, decide: propose **SELL** (if you'd
  exit today) or **HOLD** (do nothing).
```

to:

```markdown
## Step 6 — Research and decide, per shortlisted symbol

Profit-taking is no longer a per-position judgment call here — it's
fully mechanical now, driven by the weekly profit goal
(`risk_engine.evaluate_profit_exits`, surfaced through
`check-stop-losses`'s `SELL` results, covered in the stop-loss-sweep
skill). Your discretion in this step is for two things instead:

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

Change the Backtest Mode "Per-simulated-day steps" list. Change:

```markdown
- **Step 5 (refresh state with real prices):** `python -m robinhood_bot.cli
  backtest state --run RUN_ID --asof <simulated date> --prices-json
  "<quotes from Step 4>"`.
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
```

to:

```markdown
- **Step 5 (refresh state with real prices):** `python -m robinhood_bot.cli
  backtest state --run RUN_ID --asof <simulated date> --prices-json
  "<quotes from Step 4>"`.
- **Mechanical profit/stop-loss sweep (not numbered in the live cycle
  above, since it's a separate skill there):** `python -m robinhood_bot.cli
  backtest check-stop-losses --run RUN_ID --asof <simulated date>
  --prices-json "<quotes from Step 4, covering active_positions AND
  long_hold_positions>" --apply`. This reports (and applies any
  `PROMOTE_LONG_HOLD` for) stop-loss breaches, and now also reports any
  `SELL` entries from the weekly profit-goal mechanism — still
  report-only for `SELL`, so execute them via Steps 7-8 below exactly
  like every other proposed trade.
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
```

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md .claude/skills/robinhood-stop-loss-sweep/SKILL.md
git commit -m "docs: update both trading skills for the weekly profit-goal mechanism"
```

---

### Task 8: Full suite verification and a manual CLI smoke check

**Files:**
- None (verification only).

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: no new code — confirms the finished feature end to end via the real CLI, the same way this project has verified every prior feature before wrapping up.

- [ ] **Step 1: Run the full automated suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (144 tests, 0 failures, pristine output — no warnings)

- [ ] **Step 2: Manual smoke check of the new `state` fields against a scratch ledger**

```bash
D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -c "
from datetime import date
from pathlib import Path
from robinhood_bot import ledger
from robinhood_bot.portfolio_state import PortfolioState
from robinhood_bot import commands
from robinhood_bot.risk_engine import RiskConfig

path = Path('data/_scratch_ledger.json')
ledger.save_state(path, PortfolioState(cash=10_000.0))
result = commands.cmd_state(path, 10_000.0, {}, date.today(), 'paper', RiskConfig())
print(result['week'], result['week_realized_pnl'], result['week_profit_target'])
path.unlink()
"
```

Expected: prints today's ISO week (e.g. `2026-W30`), `0.0`, and `500.0` — confirming the new fields flow correctly from `RiskConfig`'s default through `cmd_state` with no ledger file left behind afterward (the script deletes its own scratch file).

- [ ] **Step 3: Confirm no regressions in existing backtest runs' CLI wiring**

```bash
D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m robinhood_bot.cli backtest trading-days --start 2026-07-06 --end 2026-07-10
```

Expected: prints the same 5 trading days this session already fetched earlier in the conversation (`2026-07-06` through `2026-07-10`), confirming `cli.py`'s existing backtest subcommands still work unchanged after Task 4's signature threading.

Note any discrepancy from the expected output above in your report rather than silently working around it.
