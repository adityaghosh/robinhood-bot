from __future__ import annotations

from datetime import date
from pathlib import Path

from . import ledger
from .portfolio_state import Position, PositionStatus, roll_month_if_needed, roll_week_if_needed
from .risk_engine import (
    RiskConfig, ExitAction, bonus_active_slots, current_weekly_tier, evaluate_buy,
    evaluate_position, evaluate_profit_exits, evaluate_sell,
)


def _position_value(position, prices: dict[str, float]) -> tuple[float, bool]:
    price = prices.get(position.symbol)
    if price is None:
        return position.cost_basis, True
    return position.qty * price, False


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
        "prior_week_realized_pnl": state.prior_week_realized_pnl,
        "effective_max_active_positions": cfg.max_active_positions + bonus_active_slots(
            state.prior_week_realized_pnl, cfg
        ),
    }


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
    if action == "sell":
        decision = evaluate_sell(state, symbol)
        return {"approved": decision.approved, "reason": decision.reason}

    raise ValueError(f"unknown action: {action}")


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
