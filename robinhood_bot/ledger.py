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
        "sector": position.sector,
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
        sector=data.get("sector"),
    )


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
