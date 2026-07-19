import argparse

from .config import Config
from .data_feed import fetch_price_history
from .runner import run


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Paper-trade a moving-average crossover strategy.")
    parser.add_argument("--symbol", default=Config.symbol)
    parser.add_argument("--period", default=Config.period)
    parser.add_argument("--interval", default=Config.interval)
    parser.add_argument("--cash", type=float, default=Config.starting_cash)
    parser.add_argument("--fast", type=int, default=Config.fast_window)
    parser.add_argument("--slow", type=int, default=Config.slow_window)
    parser.add_argument("--qty", type=int, default=Config.trade_qty)
    args = parser.parse_args()
    return Config(
        symbol=args.symbol,
        period=args.period,
        interval=args.interval,
        starting_cash=args.cash,
        fast_window=args.fast,
        slow_window=args.slow,
        trade_qty=args.qty,
    )


def main() -> None:
    config = parse_args()
    broker = run(config)

    print(f"Symbol: {config.symbol}  Period: {config.period}  Interval: {config.interval}")
    print(f"Fast/Slow SMA: {config.fast_window}/{config.slow_window}")
    print(f"Fills: {len(broker.fills)}")
    for fill in broker.fills:
        print(f"  {fill.timestamp.date()}  {fill.side:4s}  {fill.qty:4d} @ {fill.price:.2f}")

    last_price = fetch_price_history(config.symbol, config.period, config.interval)["Close"].iloc[-1]
    print(f"\nEnding cash:   ${broker.portfolio.cash:,.2f}")
    print(f"Ending shares: {broker.portfolio.shares}")
    print(f"Ending equity: ${broker.portfolio.equity(last_price):,.2f}  (started with ${config.starting_cash:,.2f})")


if __name__ == "__main__":
    main()
