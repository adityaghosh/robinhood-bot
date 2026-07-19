from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import commands, ledger
from .backtest_data import HistoricalPriceStore
from .portfolio_state import roll_month_if_needed
from .risk_engine import ExitAction, RiskConfig, evaluate_buy, evaluate_position, max_new_position_value
from .universe import average_true_range_pct, percentile_ranks, realized_volatility


@dataclass
class RunPaths:
    ledger: Path
    trade_log: Path
    equity_curve: Path


def resolve_run_paths(run_id: str, base_dir: Path) -> RunPaths:
    run_dir = base_dir / run_id
    return RunPaths(
        ledger=run_dir / "ledger.json",
        trade_log=run_dir / "trade_log.csv",
        equity_curve=run_dir / "equity_curve.csv",
    )


def cmd_backtest_state(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_state(paths.ledger, starting_cash, prices, asof, trading_mode="backtest")


def cmd_backtest_quote(symbol: str, asof: date, store: HistoricalPriceStore) -> dict:
    return {"symbol": symbol, "date": asof.isoformat(), "price": store.get_close(symbol, asof)}


def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg)


def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason,
    )


def cmd_backtest_check_stop_losses(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
    cfg: RiskConfig, apply: bool,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_check_stop_losses(paths.ledger, starting_cash, prices, asof, cfg, apply)


def cmd_backtest_trading_days(
    start: date, end: date, store: HistoricalPriceStore, benchmark_symbol: str = "SPY",
) -> dict:
    days = store.trading_days(benchmark_symbol, start, end)
    return {"trading_days": [d.isoformat() for d in days]}


def rank_candidates_as_of(
    symbols: list[str],
    store: HistoricalPriceStore,
    today: date,
    vol_window_days: int = 20,
    atr_window_days: int = 14,
) -> list[str]:
    vols: dict[str, float] = {}
    atrs: dict[str, float] = {}

    for symbol in symbols:
        closes = store.get_closes_window(symbol, today, vol_window_days + 1)
        bars = store.get_ohlc_window(symbol, today, atr_window_days + 1)
        if len(closes) < 2 or len(bars) < 2:
            continue
        vols[symbol] = realized_volatility(closes)
        atrs[symbol] = average_true_range_pct(bars)

    vol_ranks = percentile_ranks(vols)
    atr_ranks = percentile_ranks(atrs)
    scored = {s: (vol_ranks[s] + atr_ranks[s]) / 2 for s in vols}
    return sorted(scored, key=lambda s: scored[s], reverse=True)


def _append_equity_curve(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "cash", "positions_value", "total_equity"])
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _total_equity(state, store: HistoricalPriceStore, today: date) -> tuple[float, float]:
    positions_value = sum(
        (store.get_close(p.symbol, today) or p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    return state.cash, positions_value


def cmd_backtest_run(
    run_id: str,
    base_dir: Path,
    starting_cash: float,
    start: date,
    end: date,
    candidate_symbols: list[str],
    store: HistoricalPriceStore,
    cfg: RiskConfig,
    benchmark_symbol: str = "SPY",
    vol_window_days: int = 20,
    atr_window_days: int = 14,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    trading_days = store.trading_days(benchmark_symbol, start, end)

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
        if free_slots > 0:
            held = {p.symbol for p in state.active_positions + state.long_hold_positions}
            ranked = rank_candidates_as_of(candidate_symbols, store, today, vol_window_days, atr_window_days)
            for symbol in ranked:
                if free_slots <= 0:
                    break
                if symbol in held:
                    continue
                price = store.get_close(symbol, today)
                if price is None:
                    continue

                cash, positions_value = _total_equity(state, store, today)
                total_equity = cash + positions_value
                max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)
                proposed_value = min(max_value, state.cash)
                decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg)
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry",
                )
                state = ledger.load_state(paths.ledger, starting_cash)
                held.add(symbol)
                free_slots -= 1

        state = ledger.load_state(paths.ledger, starting_cash)
        cash, positions_value = _total_equity(state, store, today)
        _append_equity_curve(paths.equity_curve, {
            "date": today.isoformat(),
            "cash": cash,
            "positions_value": positions_value,
            "total_equity": cash + positions_value,
        })

    return {
        "run_id": run_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "trading_days": len(trading_days),
    }
