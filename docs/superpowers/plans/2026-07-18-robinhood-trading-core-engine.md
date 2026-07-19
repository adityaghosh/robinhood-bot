# Robinhood Trading Core Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the fully local, deterministic core of the Robinhood trading bot — position lifecycle rules, hard risk limits, ledger persistence, and a Claude-facing CLI — with zero network dependency, so it's unit-testable and drivable by hand with fake prices before any live data or MCP wiring exists.

**Architecture:** Pure-function risk logic (`risk_engine.py`) operates on plain dataclasses (`portfolio_state.py`) with no I/O. A persistence layer (`ledger.py`) serializes that state to JSON/CSV under `data/`. A thin command layer (`commands.py`) wires ledger + risk engine together into JSON-in/JSON-out operations, and `cli.py` exposes those as subcommands Claude will call via Bash in a later plan. This plan does not touch Robinhood's MCP server, live quotes, or the S&P 500/Nasdaq 100 universe — those are Plan 2.

**Tech Stack:** Python 3.11+, stdlib only (`dataclasses`, `enum`, `argparse`, `json`, `csv`, `pathlib`, `datetime`), `pytest` (already a dependency).

## Global Constraints

- No live network calls anywhere in this plan's code paths — universe/MCP integration is Plan 2.
- No new third-party dependencies. Stdlib + existing `pytest` only.
- Ledger and trade-log files live under `data/` and must be gitignored (extend `.gitignore`; it currently only ignores `data/*.csv`, not `data/*.json`).
- Follow the existing repo layout: package code in `robinhood_bot/`, tests mirrored in `tests/`.
- `risk_engine.py` is pure and deterministic: no I/O, no LLM calls, no `datetime.now()`/randomness inside its functions — callers pass `today`/prices in explicitly.
- A missing price is never fabricated or estimated — commands report it as stale/skipped (per the design spec's error-handling rule).
- Every task ends green (`pytest` passing) before moving to the next.

---

### Task 1: Position and PortfolioState data model

**Files:**
- Create: `robinhood_bot/portfolio_state.py`
- Test: `tests/test_portfolio_state.py`

**Interfaces:**
- Produces: `PositionStatus` (str Enum: `ACTIVE`, `WAITING`, `LONG_HOLD`); `Position(symbol: str, qty: float, entry_price: float, entry_date: date, status: PositionStatus, underwater_since: date | None = None)` with property `cost_basis -> float`; `PortfolioState(cash: float, active_positions: list[Position] = [], long_hold_positions: list[Position] = [], month: str = "", month_start_equity: float = 0.0)` with methods `active_slot_count() -> int`, `find_active(symbol: str) -> Position | None`, `find_long_hold(symbol: str) -> Position | None`, `is_held(symbol: str) -> bool`, `long_hold_capital() -> float`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_portfolio_state.py
from datetime import date

from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState


def test_new_portfolio_has_no_positions():
    state = PortfolioState(cash=10_000.0)
    assert state.active_slot_count() == 0
    assert state.is_held("AAPL") is False
    assert state.long_hold_capital() == 0.0


def test_active_slot_count_reflects_active_positions():
    state = PortfolioState(cash=5_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE),
        Position("MSFT", 5, 300.0, date(2026, 7, 2), PositionStatus.ACTIVE),
    ])
    assert state.active_slot_count() == 2


def test_find_active_returns_matching_position():
    position = Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    state = PortfolioState(cash=5_000.0, active_positions=[position])
    assert state.find_active("AAPL") is position
    assert state.find_active("MSFT") is None


def test_is_held_checks_both_active_and_long_hold():
    active = Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    long_hold = Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)
    state = PortfolioState(cash=5_000.0, active_positions=[active], long_hold_positions=[long_hold])
    assert state.is_held("AAPL") is True
    assert state.is_held("TSLA") is True
    assert state.is_held("NFLX") is False


def test_long_hold_capital_sums_cost_basis():
    state = PortfolioState(cash=5_000.0, long_hold_positions=[
        Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD),
        Position("NFLX", 2, 400.0, date(2026, 6, 5), PositionStatus.LONG_HOLD),
    ])
    assert state.long_hold_capital() == 5 * 200.0 + 2 * 400.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.portfolio_state'`

- [ ] **Step 3: Write minimal implementation**

```python
# robinhood_bot/portfolio_state.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class PositionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    LONG_HOLD = "LONG_HOLD"


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: date
    status: PositionStatus
    underwater_since: date | None = None

    @property
    def cost_basis(self) -> float:
        return self.qty * self.entry_price


@dataclass
class PortfolioState:
    cash: float
    active_positions: list[Position] = field(default_factory=list)
    long_hold_positions: list[Position] = field(default_factory=list)
    month: str = ""
    month_start_equity: float = 0.0

    def active_slot_count(self) -> int:
        return len(self.active_positions)

    def find_active(self, symbol: str) -> Position | None:
        for position in self.active_positions:
            if position.symbol == symbol:
                return position
        return None

    def find_long_hold(self, symbol: str) -> Position | None:
        for position in self.long_hold_positions:
            if position.symbol == symbol:
                return position
        return None

    def is_held(self, symbol: str) -> bool:
        return self.find_active(symbol) is not None or self.find_long_hold(symbol) is not None

    def long_hold_capital(self) -> float:
        return sum(position.cost_basis for position in self.long_hold_positions)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_state.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/portfolio_state.py tests/test_portfolio_state.py
git commit -m "feat: add Position/PortfolioState data model"
```

---

### Task 2: Position exit/promotion evaluation

**Files:**
- Create: `robinhood_bot/risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `Position`, `PositionStatus` from `robinhood_bot.portfolio_state` (Task 1).
- Produces: `RiskConfig` (dataclass with defaults: `max_active_positions=5, stop_loss_pct=0.05, profit_target_pct=0.08, grace_period_days=5, max_position_pct=0.20, min_position_pct=0.05, long_hold_capital_cap_pct=0.30, monthly_circuit_breaker_pct=0.10`); `ExitAction` (str Enum: `SELL`, `PROMOTE_LONG_HOLD`, `HOLD`); `PositionEvaluation(action: ExitAction, new_status: PositionStatus, new_underwater_since: date | None)`; `evaluate_position(position: Position, current_price: float, today: date, cfg: RiskConfig) -> PositionEvaluation`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_risk_engine.py
from datetime import date, timedelta

from robinhood_bot.portfolio_state import Position, PositionStatus
from robinhood_bot.risk_engine import RiskConfig, ExitAction, evaluate_position


def _position(**overrides):
    defaults = dict(
        symbol="AAPL",
        qty=10,
        entry_price=100.0,
        entry_date=date(2026, 7, 1),
        status=PositionStatus.ACTIVE,
        underwater_since=None,
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_profit_target_hit_triggers_sell():
    cfg = RiskConfig(profit_target_pct=0.08)
    position = _position(entry_price=100.0)
    result = evaluate_position(position, current_price=110.0, today=date(2026, 7, 10), cfg=cfg)
    assert result.action == ExitAction.SELL


def test_small_loss_within_stop_loss_stays_active():
    cfg = RiskConfig(stop_loss_pct=0.05, profit_target_pct=0.08)
    position = _position(entry_price=100.0)
    result = evaluate_position(position, current_price=97.0, today=date(2026, 7, 10), cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.ACTIVE
    assert result.new_underwater_since is None


def test_first_breach_of_stop_loss_enters_waiting():
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)
    position = _position(entry_price=100.0, status=PositionStatus.ACTIVE, underwater_since=None)
    today = date(2026, 7, 10)
    result = evaluate_position(position, current_price=94.0, today=today, cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.WAITING
    assert result.new_underwater_since == today


def test_waiting_within_grace_period_stays_waiting():
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)
    underwater_since = date(2026, 7, 5)
    position = _position(
        entry_price=100.0, status=PositionStatus.WAITING, underwater_since=underwater_since
    )
    today = underwater_since + timedelta(days=5)
    result = evaluate_position(position, current_price=94.0, today=today, cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.WAITING


def test_waiting_past_grace_period_promotes_to_long_hold():
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)
    underwater_since = date(2026, 7, 5)
    position = _position(
        entry_price=100.0, status=PositionStatus.WAITING, underwater_since=underwater_since
    )
    today = underwater_since + timedelta(days=6)
    result = evaluate_position(position, current_price=94.0, today=today, cfg=cfg)
    assert result.action == ExitAction.PROMOTE_LONG_HOLD
    assert result.new_status == PositionStatus.LONG_HOLD


def test_recovery_from_waiting_returns_to_active():
    cfg = RiskConfig(stop_loss_pct=0.05, profit_target_pct=0.08)
    position = _position(
        entry_price=100.0, status=PositionStatus.WAITING, underwater_since=date(2026, 7, 5)
    )
    result = evaluate_position(position, current_price=99.0, today=date(2026, 7, 8), cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.ACTIVE
    assert result.new_underwater_since is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.risk_engine'`

- [ ] **Step 3: Write minimal implementation**

```python
# robinhood_bot/risk_engine.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .portfolio_state import Position, PositionStatus


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


class ExitAction(str, Enum):
    SELL = "SELL"
    PROMOTE_LONG_HOLD = "PROMOTE_LONG_HOLD"
    HOLD = "HOLD"


@dataclass
class PositionEvaluation:
    action: ExitAction
    new_status: PositionStatus
    new_underwater_since: date | None


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

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_risk_engine.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: add position exit/promotion evaluation to risk engine"
```

---

### Task 3: Long-hold-aware position sizing

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Modify: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `RiskConfig` (Task 2).
- Produces: `max_new_position_value(total_equity: float, long_hold_capital: float, cfg: RiskConfig) -> float`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_engine.py`:

```python
from robinhood_bot.risk_engine import max_new_position_value


def test_max_position_value_at_zero_long_hold_utilization():
    cfg = RiskConfig(max_position_pct=0.20, min_position_pct=0.05, long_hold_capital_cap_pct=0.30)
    value = max_new_position_value(total_equity=10_000.0, long_hold_capital=0.0, cfg=cfg)
    assert value == 2_000.0


def test_max_position_value_at_full_long_hold_utilization():
    cfg = RiskConfig(max_position_pct=0.20, min_position_pct=0.05, long_hold_capital_cap_pct=0.30)
    value = max_new_position_value(total_equity=10_000.0, long_hold_capital=3_000.0, cfg=cfg)
    assert value == 500.0


def test_max_position_value_at_half_long_hold_utilization():
    cfg = RiskConfig(max_position_pct=0.20, min_position_pct=0.05, long_hold_capital_cap_pct=0.30)
    value = max_new_position_value(total_equity=10_000.0, long_hold_capital=1_500.0, cfg=cfg)
    assert value == 1_250.0


def test_max_position_value_zero_equity_returns_zero():
    cfg = RiskConfig()
    value = max_new_position_value(total_equity=0.0, long_hold_capital=0.0, cfg=cfg)
    assert value == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'max_new_position_value'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/risk_engine.py`:

```python
def max_new_position_value(
    total_equity: float, long_hold_capital: float, cfg: RiskConfig
) -> float:
    cap = cfg.long_hold_capital_cap_pct * total_equity
    utilization = 0.0 if cap <= 0 else min(long_hold_capital / cap, 1.0)
    pct = cfg.max_position_pct - (cfg.max_position_pct - cfg.min_position_pct) * utilization
    return pct * total_equity
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_risk_engine.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: scale max position size down as long-hold utilization rises"
```

---

### Task 4: Circuit breaker and buy approval

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Modify: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `PortfolioState` from `robinhood_bot.portfolio_state` (Task 1); `RiskConfig`, `max_new_position_value` (Tasks 2-3).
- Produces: `circuit_breaker_tripped(month_start_equity: float, current_equity: float, cfg: RiskConfig) -> bool`; `BuyDecision(approved: bool, reason: str, max_position_value: float)`; `evaluate_buy(state: PortfolioState, symbol: str, proposed_value: float, total_equity: float, cfg: RiskConfig) -> BuyDecision`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_engine.py` (add `from robinhood_bot.portfolio_state import PortfolioState` to the existing import line):

```python
from robinhood_bot.portfolio_state import PortfolioState
from robinhood_bot.risk_engine import circuit_breaker_tripped, evaluate_buy


def test_circuit_breaker_not_tripped_below_threshold():
    cfg = RiskConfig(monthly_circuit_breaker_pct=0.10)
    assert circuit_breaker_tripped(month_start_equity=10_000.0, current_equity=9_500.0, cfg=cfg) is False


def test_circuit_breaker_tripped_at_threshold():
    cfg = RiskConfig(monthly_circuit_breaker_pct=0.10)
    assert circuit_breaker_tripped(month_start_equity=10_000.0, current_equity=9_000.0, cfg=cfg) is True


def test_circuit_breaker_ignored_when_month_start_equity_zero():
    cfg = RiskConfig()
    assert circuit_breaker_tripped(month_start_equity=0.0, current_equity=9_000.0, cfg=cfg) is False


def test_evaluate_buy_rejects_when_symbol_already_held():
    cfg = RiskConfig()
    state = PortfolioState(cash=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_buy(state, "AAPL", proposed_value=500.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "already held" in decision.reason


def test_evaluate_buy_rejects_when_circuit_breaker_tripped():
    cfg = RiskConfig(monthly_circuit_breaker_pct=0.10)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=8_000.0, cfg=cfg)
    assert decision.approved is False
    assert "circuit breaker" in decision.reason


def test_evaluate_buy_rejects_when_no_active_slots():
    cfg = RiskConfig(max_active_positions=1)
    state = PortfolioState(cash=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "slots" in decision.reason


def test_evaluate_buy_rejects_when_oversized():
    cfg = RiskConfig(max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=5_000.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "exceeds max position size" in decision.reason


def test_evaluate_buy_rejects_when_insufficient_cash():
    cfg = RiskConfig(max_position_pct=0.50)
    state = PortfolioState(cash=1_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=2_000.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "insufficient cash" in decision.reason


def test_evaluate_buy_approves_happy_path():
    cfg = RiskConfig(max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is True
    assert decision.max_position_value == 2_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'circuit_breaker_tripped'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/risk_engine.py` (add `from .portfolio_state import PortfolioState` to the existing import line):

```python
from .portfolio_state import PortfolioState


def circuit_breaker_tripped(
    month_start_equity: float, current_equity: float, cfg: RiskConfig
) -> bool:
    if month_start_equity <= 0:
        return False
    drawdown = (month_start_equity - current_equity) / month_start_equity
    return drawdown >= cfg.monthly_circuit_breaker_pct


@dataclass
class BuyDecision:
    approved: bool
    reason: str
    max_position_value: float


def evaluate_buy(
    state: PortfolioState,
    symbol: str,
    proposed_value: float,
    total_equity: float,
    cfg: RiskConfig,
) -> BuyDecision:
    max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)

    if state.is_held(symbol):
        return BuyDecision(False, "symbol already held", max_value)

    if circuit_breaker_tripped(state.month_start_equity, total_equity, cfg):
        return BuyDecision(False, "monthly circuit breaker tripped", max_value)

    if state.active_slot_count() >= cfg.max_active_positions:
        return BuyDecision(False, "no active slots available", max_value)

    if proposed_value > max_value:
        return BuyDecision(
            False, f"proposed value exceeds max position size of {max_value:.2f}", max_value
        )

    if proposed_value > state.cash:
        return BuyDecision(False, "insufficient cash", max_value)

    return BuyDecision(True, "approved", max_value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_risk_engine.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: add monthly circuit breaker and buy approval logic"
```

---

### Task 5: Sell approval

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Modify: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `PortfolioState` (Task 1).
- Produces: `SellDecision(approved: bool, reason: str)`; `evaluate_sell(state: PortfolioState, symbol: str) -> SellDecision`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_engine.py`:

```python
from robinhood_bot.risk_engine import evaluate_sell


def test_evaluate_sell_approves_active_holding():
    state = PortfolioState(cash=0.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_sell(state, "AAPL")
    assert decision.approved is True


def test_evaluate_sell_approves_long_hold_holding():
    state = PortfolioState(cash=0.0, long_hold_positions=[
        Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)
    ])
    decision = evaluate_sell(state, "TSLA")
    assert decision.approved is True


def test_evaluate_sell_rejects_unheld_symbol():
    state = PortfolioState(cash=0.0)
    decision = evaluate_sell(state, "NFLX")
    assert decision.approved is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'evaluate_sell'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/risk_engine.py`:

```python
@dataclass
class SellDecision:
    approved: bool
    reason: str


def evaluate_sell(state: PortfolioState, symbol: str) -> SellDecision:
    if state.is_held(symbol):
        return SellDecision(True, "approved")
    return SellDecision(False, "symbol not currently held")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_risk_engine.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: add sell approval logic"
```

---

### Task 6: Month rollover

**Files:**
- Modify: `robinhood_bot/portfolio_state.py`
- Modify: `tests/test_portfolio_state.py`

**Interfaces:**
- Consumes: `PortfolioState` (Task 1).
- Produces: `roll_month_if_needed(state: PortfolioState, today: date, current_equity: float) -> PortfolioState` (mutates and returns `state`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_portfolio_state.py`:

```python
from robinhood_bot.portfolio_state import roll_month_if_needed


def test_roll_month_if_needed_updates_on_new_month():
    state = PortfolioState(cash=10_000.0, month="2026-06", month_start_equity=9_000.0)
    roll_month_if_needed(state, today=date(2026, 7, 1), current_equity=9_500.0)
    assert state.month == "2026-07"
    assert state.month_start_equity == 9_500.0


def test_roll_month_if_needed_no_change_within_same_month():
    state = PortfolioState(cash=10_000.0, month="2026-07", month_start_equity=9_500.0)
    roll_month_if_needed(state, today=date(2026, 7, 15), current_equity=11_000.0)
    assert state.month == "2026-07"
    assert state.month_start_equity == 9_500.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'roll_month_if_needed'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/portfolio_state.py`:

```python
def roll_month_if_needed(state: PortfolioState, today: date, current_equity: float) -> PortfolioState:
    current_month = f"{today.year:04d}-{today.month:02d}"
    if state.month != current_month:
        state.month = current_month
        state.month_start_equity = current_equity
    return state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_state.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/portfolio_state.py tests/test_portfolio_state.py
git commit -m "feat: add month rollover for circuit-breaker baseline"
```

---

### Task 7: Ledger persistence

**Files:**
- Create: `robinhood_bot/ledger.py`
- Test: `tests/test_ledger.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `Position`, `PositionStatus`, `PortfolioState` (Task 1).
- Produces: `load_state(path: Path, starting_cash: float) -> PortfolioState`; `save_state(path: Path, state: PortfolioState) -> None`; `append_trade_log(path: Path, row: dict) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ledger.py
from datetime import date

from robinhood_bot import ledger
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState


def test_load_state_returns_fresh_state_when_file_missing(tmp_path):
    path = tmp_path / "ledger.json"
    state = ledger.load_state(path, starting_cash=10_000.0)
    assert state.cash == 10_000.0
    assert state.active_positions == []


def test_save_and_load_round_trip_preserves_positions(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(
        cash=8_000.0,
        active_positions=[
            Position(
                "AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.WAITING,
                underwater_since=date(2026, 7, 5),
            )
        ],
        long_hold_positions=[
            Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)
        ],
        month="2026-07",
        month_start_equity=10_000.0,
    )
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.cash == 8_000.0
    assert loaded.month == "2026-07"
    assert loaded.active_positions[0].symbol == "AAPL"
    assert loaded.active_positions[0].status == PositionStatus.WAITING
    assert loaded.active_positions[0].underwater_since == date(2026, 7, 5)
    assert loaded.long_hold_positions[0].symbol == "TSLA"


def test_append_trade_log_writes_header_once(tmp_path):
    path = tmp_path / "trade_log.csv"
    ledger.append_trade_log(path, {
        "timestamp": "2026-07-01", "action": "BUY", "symbol": "AAPL",
        "qty": 10, "price": 100.0, "reason": "test",
    })
    ledger.append_trade_log(path, {
        "timestamp": "2026-07-02", "action": "SELL", "symbol": "AAPL",
        "qty": 10, "price": 110.0, "reason": "test",
    })
    contents = path.read_text().splitlines()
    assert contents[0] == "timestamp,action,symbol,qty,price,reason"
    assert len(contents) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.ledger'`

- [ ] **Step 3: Write minimal implementation**

```python
# robinhood_bot/ledger.py
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from .portfolio_state import Position, PositionStatus, PortfolioState


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
    }


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
    )


def state_to_dict(state: PortfolioState) -> dict:
    return {
        "cash": state.cash,
        "active_positions": [_position_to_dict(p) for p in state.active_positions],
        "long_hold_positions": [_position_to_dict(p) for p in state.long_hold_positions],
        "month": state.month,
        "month_start_equity": state.month_start_equity,
    }


def state_from_dict(data: dict) -> PortfolioState:
    return PortfolioState(
        cash=data["cash"],
        active_positions=[_position_from_dict(p) for p in data["active_positions"]],
        long_hold_positions=[_position_from_dict(p) for p in data["long_hold_positions"]],
        month=data.get("month", ""),
        month_start_equity=data.get("month_start_equity", 0.0),
    )


def load_state(path: Path, starting_cash: float) -> PortfolioState:
    if not path.exists():
        return PortfolioState(cash=starting_cash)
    with path.open("r") as f:
        return state_from_dict(json.load(f))


def save_state(path: Path, state: PortfolioState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(state_to_dict(state), f, indent=2)


def append_trade_log(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "action", "symbol", "qty", "price", "reason"]
        )
        if is_new:
            writer.writeheader()
        writer.writerow(row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ledger.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Gitignore the ledger JSON file and commit**

Edit `.gitignore`, changing:

```
data/*.csv
```

to:

```
data/*.csv
data/*.json
```

```bash
git add robinhood_bot/ledger.py tests/test_ledger.py .gitignore
git commit -m "feat: add ledger persistence for portfolio state and trade log"
```

---

### Task 8: `commands.cmd_state`

**Files:**
- Create: `robinhood_bot/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `ledger.load_state`, `ledger.save_state` (Task 7); `roll_month_if_needed` (Task 6).
- Produces: `cmd_state(ledger_path: Path, starting_cash: float, prices: dict[str, float], today: date) -> dict` returning `{"cash", "active_positions": [...], "long_hold_positions": [...], "total_equity", "month", "month_start_equity", "monthly_return_pct"}`, where each position entry has `{"symbol", "qty", "entry_price", "entry_date", "status", "current_value", "unrealized_pnl_pct", "stale_price"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commands.py
from datetime import date

from robinhood_bot import commands, ledger
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState


def test_cmd_state_computes_total_equity_and_pnl(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        month="2026-07",
        month_start_equity=10_000.0,
    )
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10)
    )

    assert result["cash"] == 5_000.0
    assert result["active_positions"][0]["current_value"] == 1_100.0
    assert result["active_positions"][0]["unrealized_pnl_pct"] == 0.1
    assert result["total_equity"] == 6_100.0


def test_cmd_state_marks_missing_price_as_stale(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    )
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10))

    assert result["active_positions"][0]["stale_price"] is True
    assert result["active_positions"][0]["current_value"] == 1_000.0
    assert result["active_positions"][0]["unrealized_pnl_pct"] is None


def test_cmd_state_rolls_month_and_persists(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(cash=10_000.0, month="2026-06", month_start_equity=9_000.0)
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 1))

    assert result["month"] == "2026-07"
    assert result["month_start_equity"] == 10_000.0

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.month == "2026-07"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.commands'`

- [ ] **Step 3: Write minimal implementation**

```python
# robinhood_bot/commands.py
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import ledger
from .portfolio_state import roll_month_if_needed


def _position_value(position, prices: dict[str, float]) -> tuple[float, bool]:
    price = prices.get(position.symbol)
    if price is None:
        return position.cost_basis, True
    return position.qty * price, False


def _position_summary(position, prices: dict[str, float]) -> dict:
    value, stale = _position_value(position, prices)
    pnl_pct = None if stale else (value / position.cost_basis - 1.0)
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


def cmd_state(ledger_path: Path, starting_cash: float, prices: dict[str, float], today: date) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    active_out = [_position_summary(p, prices) for p in state.active_positions]
    long_hold_out = [_position_summary(p, prices) for p in state.long_hold_positions]
    positions_value = sum(o["current_value"] for o in active_out + long_hold_out)
    total_equity = state.cash + positions_value

    roll_month_if_needed(state, today, total_equity)
    ledger.save_state(ledger_path, state)

    return {
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

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: add cmd_state command"
```

---

### Task 9: `commands.cmd_risk_check`

**Files:**
- Modify: `robinhood_bot/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Consumes: `RiskConfig`, `evaluate_buy`, `evaluate_sell` from `robinhood_bot.risk_engine` (Tasks 2, 4, 5).
- Produces: `cmd_risk_check(ledger_path: Path, starting_cash: float, action: str, symbol: str, proposed_value: float, prices: dict[str, float], cfg: RiskConfig) -> dict` returning `{"approved", "reason"}` (buy also includes `"max_position_value"`); raises `ValueError` for an unknown action.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py` (add `import pytest` and `from robinhood_bot.risk_engine import RiskConfig` to the imports):

```python
import pytest

from robinhood_bot.risk_engine import RiskConfig


def test_cmd_risk_check_buy_approves_happy_path(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, month_start_equity=10_000.0))
    cfg = RiskConfig(max_position_pct=0.20)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={}, cfg=cfg,
    )

    assert result["approved"] is True
    assert result["max_position_value"] == 2_000.0


def test_cmd_risk_check_buy_rejects_when_slots_full(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=10_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        month_start_equity=10_000.0,
    ))
    cfg = RiskConfig(max_active_positions=1)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=500.0, prices={"AAPL": 100.0}, cfg=cfg,
    )

    assert result["approved"] is False


def test_cmd_risk_check_sell_rejects_unheld_symbol(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))
    cfg = RiskConfig()

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="sell", symbol="NFLX",
        proposed_value=0.0, prices={}, cfg=cfg,
    )

    assert result["approved"] is False


def test_cmd_risk_check_unknown_action_raises(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))
    cfg = RiskConfig()

    with pytest.raises(ValueError):
        commands.cmd_risk_check(
            ledger_path, starting_cash=0.0, action="hold", symbol="AAPL",
            proposed_value=0.0, prices={}, cfg=cfg,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.commands' has no attribute 'cmd_risk_check'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/commands.py` (add `from .risk_engine import RiskConfig, evaluate_buy, evaluate_sell` to the imports):

```python
from .risk_engine import RiskConfig, evaluate_buy, evaluate_sell


def cmd_risk_check(
    ledger_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    proposed_value: float,
    prices: dict[str, float],
    cfg: RiskConfig,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    positions_value = sum(
        prices.get(p.symbol, p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    total_equity = state.cash + positions_value

    if action == "buy":
        decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg)
        return {
            "approved": decision.approved,
            "reason": decision.reason,
            "max_position_value": decision.max_position_value,
        }
    if action == "sell":
        decision = evaluate_sell(state, symbol)
        return {"approved": decision.approved, "reason": decision.reason}

    raise ValueError(f"unknown action: {action}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: add cmd_risk_check command"
```

---

### Task 10: `commands.cmd_record_fill`

**Files:**
- Modify: `robinhood_bot/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Consumes: `Position`, `PositionStatus` from `robinhood_bot.portfolio_state` (Task 1); `ledger.append_trade_log` (Task 7).
- Produces: `cmd_record_fill(ledger_path: Path, trade_log_path: Path, starting_cash: float, action: str, symbol: str, qty: float, price: float, today: date, reason: str) -> dict` returning `{"cash", "action", "symbol", "qty", "price"}`; raises `ValueError` on an already-held buy, insufficient cash, an unheld sell, or an unknown action.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
def test_cmd_record_fill_buy_updates_cash_and_adds_position(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    result = commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle",
    )

    assert result["cash"] == 8_500.0
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.cash == 8_500.0
    assert reloaded.active_positions[0].symbol == "MSFT"
    assert trade_log_path.exists()


def test_cmd_record_fill_buy_rejects_insufficient_cash(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=100.0))

    with pytest.raises(ValueError):
        commands.cmd_record_fill(
            ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
            qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle",
        )


def test_cmd_record_fill_sell_removes_position_and_credits_cash(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))

    result = commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=110.0, today=date(2026, 7, 10), reason="profit target",
    )

    assert result["cash"] == 2_100.0
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions == []


def test_cmd_record_fill_sell_unheld_symbol_raises(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=1_000.0))

    with pytest.raises(ValueError):
        commands.cmd_record_fill(
            ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="NFLX",
            qty=1, price=10.0, today=date(2026, 7, 10), reason="test",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.commands' has no attribute 'cmd_record_fill'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/commands.py` (add `from .portfolio_state import Position, PositionStatus, roll_month_if_needed` — replacing the existing `roll_month_if_needed`-only import line):

```python
from .portfolio_state import Position, PositionStatus, roll_month_if_needed


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
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

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
            )
        )
    elif action == "sell":
        position = state.find_active(symbol) or state.find_long_hold(symbol)
        if position is None:
            raise ValueError(f"{symbol} not currently held")
        state.cash += position.qty * price
        if position in state.active_positions:
            state.active_positions.remove(position)
        else:
            state.long_hold_positions.remove(position)
    else:
        raise ValueError(f"unknown action: {action}")

    ledger.save_state(ledger_path, state)
    ledger.append_trade_log(
        trade_log_path,
        {
            "timestamp": today.isoformat(),
            "action": action.upper(),
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "reason": reason,
        },
    )

    return {"cash": state.cash, "action": action, "symbol": symbol, "qty": qty, "price": price}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: add cmd_record_fill command"
```

---

### Task 11: `commands.cmd_check_stop_losses`

**Files:**
- Modify: `robinhood_bot/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Consumes: `ExitAction`, `evaluate_position` from `robinhood_bot.risk_engine` (Task 2).
- Produces: `cmd_check_stop_losses(ledger_path: Path, starting_cash: float, prices: dict[str, float], today: date, cfg: RiskConfig, apply: bool) -> dict` returning `{"results": [{"symbol", "action", ...}], "applied": bool}`. `SELL` results are reported only — never remove the position (that happens via `cmd_record_fill`). `apply=False` never calls `ledger.save_state`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
def test_check_stop_losses_skips_symbol_without_fresh_price(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig()

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), cfg=cfg, apply=True,
    )

    assert result["results"][0]["action"] == "SKIP"
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].status == PositionStatus.ACTIVE


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


def test_check_stop_losses_promotes_expired_position_to_long_hold(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position(
            "AAPL", 10, 100.0, date(2026, 6, 1), PositionStatus.WAITING,
            underwater_since=date(2026, 7, 1),
        )],
    ))
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"AAPL": 90.0}, today=date(2026, 7, 8), cfg=cfg, apply=True,
    )

    assert result["results"][0]["action"] == "PROMOTE_LONG_HOLD"
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions == []
    assert reloaded.long_hold_positions[0].symbol == "AAPL"
    assert reloaded.long_hold_positions[0].status == PositionStatus.LONG_HOLD


def test_check_stop_losses_dry_run_does_not_save(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)

    commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"AAPL": 90.0}, today=date(2026, 7, 8), cfg=cfg, apply=False,
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].status == PositionStatus.ACTIVE
    assert reloaded.active_positions[0].underwater_since is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL with `AttributeError: module 'robinhood_bot.commands' has no attribute 'cmd_check_stop_losses'`

- [ ] **Step 3: Write minimal implementation**

Append to `robinhood_bot/commands.py` (add `from .risk_engine import RiskConfig, ExitAction, evaluate_buy, evaluate_position, evaluate_sell` — replacing the existing risk_engine import line):

```python
from .risk_engine import RiskConfig, ExitAction, evaluate_buy, evaluate_position, evaluate_sell


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

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: add cmd_check_stop_losses command"
```

---

### Task 12: `cli.py` wiring

**Files:**
- Create: `robinhood_bot/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `commands.cmd_state`, `commands.cmd_risk_check`, `commands.cmd_record_fill`, `commands.cmd_check_stop_losses` (Tasks 8-11); `risk_engine.RiskConfig` (Task 2).
- Produces: `main(argv: list[str] | None = None) -> int` — module-level `LEDGER_PATH`, `TRADE_LOG_PATH`, `STARTING_CASH` constants (overridable via `monkeypatch.setattr` in tests); subcommands `state`, `risk-check {buy|sell} SYMBOL [--value] [--prices-json]`, `record-fill {buy|sell} SYMBOL --qty --price [--reason]`, `check-stop-losses --prices-json [--apply]`. Prints the command's JSON result to stdout and returns `0` on success.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import json

from robinhood_bot import cli


def test_cli_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")

    exit_code = cli.main(["state", "--prices-json", "{}"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["cash"] == cli.STARTING_CASH
    assert output["active_positions"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'robinhood_bot.cli'`

- [ ] **Step 3: Write minimal implementation**

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

LEDGER_PATH = Path("data/ledger.json")
TRADE_LOG_PATH = Path("data/trade_log.csv")
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
    else:
        result = commands.cmd_check_stop_losses(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, cfg, args.apply,
        )

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -v`
Expected: PASS (all tests across `test_portfolio_state.py`, `test_risk_engine.py`, `test_ledger.py`, `test_commands.py`, `test_cli.py`, plus the pre-existing `test_moving_average.py`)

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: add CLI wiring for state/risk-check/record-fill/check-stop-losses"
```

---

## What This Plan Does Not Cover (deferred to Plan 2)

- `universe.py` — dynamic S&P 500 / Nasdaq 100 / leveraged-fund universe fetch and volatility ranking.
- The two `.claude/skills/` SKILL.md procedures (daily cycle, stop-loss sweep).
- Any Robinhood MCP tool usage (`get_equity_quotes`, `place_equity_order`, etc.) — this plan's CLI only ever receives prices as arguments from the caller, never fetches them itself.
- `TRADING_MODE` (paper/live) wiring — this plan's `cmd_record_fill` always simulates; the live-order branch is introduced in Plan 2 alongside the skills that decide which mode to invoke it in.
