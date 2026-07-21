# robinhood_bot/cli.py
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import backtest_commands, commands, ledger
from .backtest_data import HistoricalPriceStore
from .risk_engine import RiskConfig
from .universe import UniverseConfig, build_universe, is_bullish_ma_trend, relative_strength_index
from .universe_client import LiveHistoricalDataFetcher, LiveMarketDataClient

LEDGER_PATH = Path("data/ledger.json")
TRADE_LOG_PATH = Path("data/trade_log.csv")
UNIVERSE_CACHE_PATH = Path("data/universe_cache.json")
SECTOR_CACHE_PATH = Path("data/sector_cache.json")
BACKTEST_BASE_DIR = Path("data/backtests")
HISTORICAL_CACHE_DIR = Path("data/historical_price_cache")
STARTING_CASH = 10_000.0
TRADING_MODE = "paper"
BENCHMARK_SYMBOL = "SPY"


def _parse_prices(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    return json.loads(raw)


def _parse_closes(raw: str | None) -> dict[str, list[float]]:
    if not raw:
        return {}
    return json.loads(raw)


def _build_price_store() -> HistoricalPriceStore:
    return HistoricalPriceStore(LiveHistoricalDataFetcher(), HISTORICAL_CACHE_DIR)


def _dispatch_backtest(args) -> dict:
    cfg = RiskConfig()

    if args.backtest_command == "state":
        return backtest_commands.cmd_backtest_state(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof), cfg, _build_price_store(),
        )
    if args.backtest_command == "quote":
        return backtest_commands.cmd_backtest_quote(
            args.symbol, date.fromisoformat(args.asof), _build_price_store(),
        )
    if args.backtest_command == "risk-check":
        return backtest_commands.cmd_backtest_risk_check(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
    if args.backtest_command == "record-fill":
        return backtest_commands.cmd_backtest_record_fill(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, date.fromisoformat(args.asof), args.reason, cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
    if args.backtest_command == "check-stop-losses":
        return backtest_commands.cmd_backtest_check_stop_losses(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof), cfg, args.apply,
        )
    if args.backtest_command == "mark-day":
        return backtest_commands.cmd_backtest_mark_day(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, _parse_prices(args.prices_json),
            date.fromisoformat(args.asof),
        )
    if args.backtest_command == "trading-days":
        return backtest_commands.cmd_backtest_trading_days(
            date.fromisoformat(args.start), date.fromisoformat(args.end), _build_price_store(),
            BENCHMARK_SYMBOL,
        )
    if args.backtest_command == "run":
        store = _build_price_store()
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, SECTOR_CACHE_PATH, UniverseConfig(), date.today(),
        )
        candidate_sectors = {c.symbol: c.sector for c in candidates if c.sector is not None}
        return backtest_commands.cmd_backtest_run(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, date.fromisoformat(args.start),
            date.fromisoformat(args.end), [c.symbol for c in candidates], candidate_sectors, store, cfg,
            BENCHMARK_SYMBOL,
        )
    if args.backtest_command == "report":
        return backtest_commands.cmd_backtest_report(
            args.run, BACKTEST_BASE_DIR, _build_price_store(), BENCHMARK_SYMBOL,
        )

    raise ValueError(f"unknown backtest command: {args.backtest_command}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robinhood_bot.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_state = sub.add_parser("state")
    p_state.add_argument("--prices-json", default=None)
    p_state.add_argument("--closes-json", default=None)

    p_risk = sub.add_parser("risk-check")
    p_risk.add_argument("action", choices=["buy", "sell"])
    p_risk.add_argument("symbol")
    p_risk.add_argument("--value", type=float, default=0.0)
    p_risk.add_argument("--prices-json", default=None)
    p_risk.add_argument("--sector", default=None)
    p_risk.add_argument("--rsi", type=float, default=50.0)
    p_risk.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_risk.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)

    p_fill = sub.add_parser("record-fill")
    p_fill.add_argument("action", choices=["buy", "sell"])
    p_fill.add_argument("symbol")
    p_fill.add_argument("--qty", type=float, required=True)
    p_fill.add_argument("--price", type=float, required=True)
    p_fill.add_argument("--reason", default="")
    p_fill.add_argument("--sector", default=None)
    p_fill.add_argument("--rsi", type=float, default=50.0)
    p_fill.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_fill.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)

    p_stop = sub.add_parser("check-stop-losses")
    p_stop.add_argument("--prices-json", required=True)
    p_stop.add_argument("--apply", action="store_true")

    p_universe = sub.add_parser("universe")
    p_universe.add_argument("--refresh", action="store_true")
    p_universe.add_argument("--mode", choices=["realized_vol", "atr_pct", "both"], default=None)

    p_backtest = sub.add_parser("backtest")
    backtest_sub = p_backtest.add_subparsers(dest="backtest_command", required=True)

    p_bt_state = backtest_sub.add_parser("state")
    p_bt_state.add_argument("--run", required=True)
    p_bt_state.add_argument("--asof", required=True)
    p_bt_state.add_argument("--prices-json", default=None)

    p_bt_quote = backtest_sub.add_parser("quote")
    p_bt_quote.add_argument("symbol")
    p_bt_quote.add_argument("--asof", required=True)

    p_bt_risk = backtest_sub.add_parser("risk-check")
    p_bt_risk.add_argument("action", choices=["buy", "sell"])
    p_bt_risk.add_argument("symbol")
    p_bt_risk.add_argument("--run", required=True)
    p_bt_risk.add_argument("--asof", required=True)
    p_bt_risk.add_argument("--value", type=float, default=0.0)
    p_bt_risk.add_argument("--prices-json", default=None)
    p_bt_risk.add_argument("--sector", default=None)
    p_bt_risk.add_argument("--rsi", type=float, default=50.0)
    p_bt_risk.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_bt_risk.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)

    p_bt_fill = backtest_sub.add_parser("record-fill")
    p_bt_fill.add_argument("action", choices=["buy", "sell"])
    p_bt_fill.add_argument("symbol")
    p_bt_fill.add_argument("--run", required=True)
    p_bt_fill.add_argument("--asof", required=True)
    p_bt_fill.add_argument("--qty", type=float, required=True)
    p_bt_fill.add_argument("--price", type=float, required=True)
    p_bt_fill.add_argument("--reason", default="")
    p_bt_fill.add_argument("--sector", default=None)
    p_bt_fill.add_argument("--rsi", type=float, default=50.0)
    p_bt_fill.add_argument("--ma-bullish", dest="ma_bullish", action=argparse.BooleanOptionalAction, default=None)
    p_bt_fill.add_argument("--golden-cross-bullish", dest="golden_cross_bullish", action=argparse.BooleanOptionalAction, default=None)

    p_bt_stop = backtest_sub.add_parser("check-stop-losses")
    p_bt_stop.add_argument("--run", required=True)
    p_bt_stop.add_argument("--asof", required=True)
    p_bt_stop.add_argument("--prices-json", required=True)
    p_bt_stop.add_argument("--apply", action="store_true")

    p_bt_mark = backtest_sub.add_parser("mark-day")
    p_bt_mark.add_argument("--run", required=True)
    p_bt_mark.add_argument("--asof", required=True)
    p_bt_mark.add_argument("--prices-json", default=None)

    p_bt_run = backtest_sub.add_parser("run")
    p_bt_run.add_argument("--run", required=True)
    p_bt_run.add_argument("--start", required=True)
    p_bt_run.add_argument("--end", required=True)

    p_bt_report = backtest_sub.add_parser("report")
    p_bt_report.add_argument("--run", required=True)

    p_bt_days = backtest_sub.add_parser("trading-days")
    p_bt_days.add_argument("--start", required=True)
    p_bt_days.add_argument("--end", required=True)

    args = parser.parse_args(argv)
    today = date.today()
    cfg = RiskConfig()

    if args.command == "state":
        universe_cfg = UniverseConfig()
        held_state = ledger.load_state(LEDGER_PATH, STARTING_CASH)
        held_symbols = {p.symbol for p in held_state.active_positions + held_state.long_hold_positions}
        closes_by_symbol = _parse_closes(args.closes_json)
        rsi_by_symbol: dict[str, float] = {}
        ma_trend_by_symbol: dict[str, bool | None] = {}
        golden_cross_by_symbol: dict[str, bool | None] = {}
        if closes_by_symbol:
            for symbol in held_symbols:
                closes = closes_by_symbol.get(symbol)
                if closes is None:
                    continue
                rsi_by_symbol[symbol] = relative_strength_index(closes, universe_cfg.rsi_window_days)
                ma_trend_by_symbol[symbol] = is_bullish_ma_trend(
                    closes, universe_cfg.ma_short_window_days, universe_cfg.ma_long_window_days
                )
                golden_cross_by_symbol[symbol] = is_bullish_ma_trend(
                    closes, universe_cfg.golden_cross_short_window_days, universe_cfg.golden_cross_long_window_days
                )
        else:
            market_client = LiveMarketDataClient()
            lookback = max(
                universe_cfg.rsi_window_days + 1, universe_cfg.ma_long_window_days,
                universe_cfg.golden_cross_long_window_days,
            ) + 5
            for symbol in held_symbols:
                bars = market_client.fetch_daily_bars(symbol, lookback)
                closes = [bar.close for bar in bars]
                rsi_by_symbol[symbol] = relative_strength_index(closes, universe_cfg.rsi_window_days)
                ma_trend_by_symbol[symbol] = is_bullish_ma_trend(
                    closes, universe_cfg.ma_short_window_days, universe_cfg.ma_long_window_days
                )
                golden_cross_by_symbol[symbol] = is_bullish_ma_trend(
                    closes, universe_cfg.golden_cross_short_window_days, universe_cfg.golden_cross_long_window_days
                )
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE, cfg,
            rsi_by_symbol=rsi_by_symbol, ma_trend_by_symbol=ma_trend_by_symbol,
            golden_cross_by_symbol=golden_cross_by_symbol,
        )
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason, cfg, sector=args.sector,
            rsi=args.rsi, ma_trend_bullish=args.ma_bullish, golden_cross_bullish=args.golden_cross_bullish,
        )
    elif args.command == "check-stop-losses":
        result = commands.cmd_check_stop_losses(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, cfg, args.apply,
        )
    elif args.command == "backtest":
        result = _dispatch_backtest(args)
    else:
        universe_cfg = UniverseConfig()
        if args.mode:
            universe_cfg.ranking_mode = args.mode
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, SECTOR_CACHE_PATH, universe_cfg, today, args.refresh
        )
        result = {
            "candidates": [
                {
                    "symbol": c.symbol,
                    "category": c.category,
                    "market_cap": c.market_cap,
                    "realized_vol": c.realized_vol,
                    "atr_pct": c.atr_pct,
                    "combined_rank": c.combined_rank,
                    "sector": c.sector,
                    "rsi": c.rsi,
                    "ma_trend_bullish": c.ma_trend_bullish,
                    "golden_cross_bullish": c.golden_cross_bullish,
                }
                for c in candidates
            ]
        }

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
