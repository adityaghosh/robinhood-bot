from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import commands
from .backtest_data import HistoricalPriceStore
from .risk_engine import RiskConfig
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
