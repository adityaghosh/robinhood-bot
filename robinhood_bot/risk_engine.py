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
