from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .portfolio_state import Position, PositionStatus, PortfolioState


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


def max_new_position_value(
    total_equity: float, long_hold_capital: float, cfg: RiskConfig
) -> float:
    cap = cfg.long_hold_capital_cap_pct * total_equity
    utilization = 0.0 if cap <= 0 else min(long_hold_capital / cap, 1.0)
    pct = cfg.max_position_pct - (cfg.max_position_pct - cfg.min_position_pct) * utilization
    return pct * total_equity


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
