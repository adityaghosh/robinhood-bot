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


def roll_month_if_needed(state: PortfolioState, today: date, current_equity: float) -> PortfolioState:
    current_month = f"{today.year:04d}-{today.month:02d}"
    if state.month != current_month:
        state.month = current_month
        state.month_start_equity = current_equity
    return state
