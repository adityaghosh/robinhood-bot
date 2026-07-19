# robinhood_bot/cli.py
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import commands
from .risk_engine import RiskConfig
from .universe import UniverseConfig, build_universe
from .universe_client import LiveMarketDataClient

LEDGER_PATH = Path("data/ledger.json")
TRADE_LOG_PATH = Path("data/trade_log.csv")
UNIVERSE_CACHE_PATH = Path("data/universe_cache.json")
STARTING_CASH = 10_000.0


def _parse_prices(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robinhood_bot.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("state").add_argument("--prices-json", default=None)

    p_risk = sub.add_parser("risk-check")
    p_risk.add_argument("action", choices=["buy", "sell"])
    p_risk.add_argument("symbol")
    p_risk.add_argument("--value", type=float, default=0.0)
    p_risk.add_argument("--prices-json", default=None)

    p_fill = sub.add_parser("record-fill")
    p_fill.add_argument("action", choices=["buy", "sell"])
    p_fill.add_argument("symbol")
    p_fill.add_argument("--qty", type=float, required=True)
    p_fill.add_argument("--price", type=float, required=True)
    p_fill.add_argument("--reason", default="")

    p_stop = sub.add_parser("check-stop-losses")
    p_stop.add_argument("--prices-json", required=True)
    p_stop.add_argument("--apply", action="store_true")

    p_universe = sub.add_parser("universe")
    p_universe.add_argument("--refresh", action="store_true")
    p_universe.add_argument("--mode", choices=["realized_vol", "atr_pct", "both"], default=None)

    args = parser.parse_args(argv)
    today = date.today()
    cfg = RiskConfig()

    if args.command == "state":
        result = commands.cmd_state(LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today)
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason,
        )
    elif args.command == "check-stop-losses":
        result = commands.cmd_check_stop_losses(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, cfg, args.apply,
        )
    else:
        universe_cfg = UniverseConfig()
        if args.mode:
            universe_cfg.ranking_mode = args.mode
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, universe_cfg, today, args.refresh
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
                }
                for c in candidates
            ]
        }

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
