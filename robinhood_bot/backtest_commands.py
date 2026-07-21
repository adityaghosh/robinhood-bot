from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import commands, ledger
from .backtest_data import HistoricalPriceStore
from .portfolio_state import roll_month_if_needed, roll_week_if_needed
from .risk_engine import (
    ExitAction, RiskConfig, bonus_active_slots, evaluate_buy, evaluate_position,
    evaluate_profit_exits, max_new_position_value,
)
from .universe import (
    average_true_range_pct, is_bullish_ma_trend, percentile_ranks, realized_volatility,
    relative_strength_index,
)


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
    cfg: RiskConfig, store: HistoricalPriceStore,
    rsi_window_days: int = 14, ma_short_window_days: int = 5, ma_long_window_days: int = 20,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    state = ledger.load_state(paths.ledger, starting_cash)
    held_symbols = {p.symbol for p in state.active_positions + state.long_hold_positions}

    lookback = max(rsi_window_days + 1, ma_long_window_days)
    rsi_by_symbol: dict[str, float] = {}
    ma_trend_by_symbol: dict[str, bool | None] = {}
    for symbol in held_symbols:
        closes = store.get_closes_window(symbol, asof, lookback)
        rsi_by_symbol[symbol] = relative_strength_index(closes, rsi_window_days)
        ma_trend_by_symbol[symbol] = is_bullish_ma_trend(closes, ma_short_window_days, ma_long_window_days)

    return commands.cmd_state(
        paths.ledger, starting_cash, prices, asof, trading_mode="backtest", cfg=cfg,
        rsi_by_symbol=rsi_by_symbol, ma_trend_by_symbol=ma_trend_by_symbol,
    )


def cmd_backtest_quote(symbol: str, asof: date, store: HistoricalPriceStore) -> dict:
    return {"symbol": symbol, "date": asof.isoformat(), "price": store.get_close(symbol, asof)}


def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig, sector: str | None = None,
    rsi: float = 50.0, ma_trend_bullish: bool | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(
        paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg, sector, rsi, ma_trend_bullish,
    )


def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str, sector: str | None = None,
    rsi: float = 50.0, ma_trend_bullish: bool | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason, sector,
        rsi, ma_trend_bullish,
    )


def cmd_backtest_check_stop_losses(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
    cfg: RiskConfig, apply: bool,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_check_stop_losses(paths.ledger, starting_cash, prices, asof, cfg, apply)


def cmd_backtest_mark_day(
    run_id: str, base_dir: Path, starting_cash: float, prices: dict[str, float], asof: date,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    state = ledger.load_state(paths.ledger, starting_cash)
    positions_value = sum(
        prices.get(p.symbol, p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    row = {
        "date": asof.isoformat(),
        "cash": state.cash,
        "positions_value": positions_value,
        "total_equity": state.cash + positions_value,
    }
    _append_equity_curve(paths.equity_curve, row)
    return row


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
    candidate_sectors: dict[str, str],
    store: HistoricalPriceStore,
    cfg: RiskConfig,
    benchmark_symbol: str = "SPY",
    vol_window_days: int = 20,
    atr_window_days: int = 14,
    rsi_window_days: int = 14,
    ma_short_window_days: int = 5,
    ma_long_window_days: int = 20,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    trading_days = store.trading_days(benchmark_symbol, start, end)

    # Prefetch each candidate's full needed range up front so the day-by-day
    # `rank_candidates_as_of` calls below always hit an already-warm cache
    # instead of triggering one `_ensure_range` fetch per symbol per day
    # (which would make a cold multi-year run O(days x symbols) network
    # calls instead of O(symbols)). The lookback buffer must cover the
    # FIRST day's ranking window too, not just subsequent days, and must
    # match `get_ohlc_window`/`get_closes_window`'s own buffer formula
    # (`window_days * 2 + 10`, where `window_days` there is
    # `vol_window_days + 1` / `atr_window_days + 1`) or the first call on
    # day one would still fall outside the prefetched range and refetch.
    lookback_days = (max(vol_window_days, atr_window_days) + 1) * 2 + 10
    for symbol in candidate_symbols:
        store.prefetch(symbol, start - timedelta(days=lookback_days), end)

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
        # Must match evaluate_buy's own effective-cap check (base cap + any
        # bonus slots earned from last week's profit surplus), or a bonus
        # week would be silently under-filled here even though evaluate_buy
        # itself would have approved the extra buy.
        effective_max_active_positions = cfg.max_active_positions + bonus_active_slots(
            state.prior_week_realized_pnl, cfg
        )
        free_slots = effective_max_active_positions - state.active_slot_count()
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
                sector = candidate_sectors.get(symbol)
                # RSI/MA trend change daily (unlike sector, which is a
                # permanent fact about a symbol) so they can't be
                # precomputed once per run -- fetch fresh here, immediately
                # before the buy decision, mirroring how `price` itself is
                # fetched fresh per candidate per day just above.
                indicator_lookback = max(rsi_window_days + 1, ma_long_window_days)
                closes = store.get_closes_window(symbol, today, indicator_lookback)
                rsi = relative_strength_index(closes, rsi_window_days)
                ma_trend_bullish = is_bullish_ma_trend(closes, ma_short_window_days, ma_long_window_days)
                decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector, rsi, ma_trend_bullish)
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry", sector, rsi, ma_trend_bullish,
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


def cmd_backtest_report(
    run_id: str, base_dir: Path, store: HistoricalPriceStore, benchmark_symbol: str = "SPY",
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)

    if not paths.equity_curve.exists():
        raise ValueError(f"no equity curve data for run {run_id!r} — has `backtest run` been executed?")

    with paths.equity_curve.open() as f:
        equity_rows = list(csv.DictReader(f))

    starting_equity = float(equity_rows[0]["total_equity"])
    ending_equity = float(equity_rows[-1]["total_equity"])
    total_return_pct = ending_equity / starting_equity - 1.0

    peak = starting_equity
    max_drawdown_pct = 0.0
    for row in equity_rows:
        equity = float(row["total_equity"])
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

    wins = 0
    losses = 0
    if paths.trade_log.exists():
        with paths.trade_log.open() as f:
            trade_rows = list(csv.DictReader(f))
        open_buys: dict[str, dict] = {}
        for row in trade_rows:
            if row["action"] == "BUY":
                open_buys[row["symbol"]] = row
            elif row["action"] == "SELL":
                buy_row = open_buys.pop(row["symbol"], None)
                if buy_row is None:
                    continue
                pnl = (float(row["price"]) - float(buy_row["price"])) * float(row["qty"])
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1

    start_date = date.fromisoformat(equity_rows[0]["date"])
    end_date = date.fromisoformat(equity_rows[-1]["date"])
    benchmark_start = store.get_close(benchmark_symbol, start_date)
    benchmark_end = store.get_close(benchmark_symbol, end_date)
    benchmark_return_pct = (
        (benchmark_end / benchmark_start - 1.0) if benchmark_start and benchmark_end else None
    )

    return {
        "run_id": run_id,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "wins": wins,
        "losses": losses,
        "benchmark_symbol": benchmark_symbol,
        "benchmark_return_pct": benchmark_return_pct,
    }
