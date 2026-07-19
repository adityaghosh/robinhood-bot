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
