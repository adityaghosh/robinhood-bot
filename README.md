# robinhood-bot

Paper-trading bot scaffold. No live Robinhood account connection — this simulates
trades against real market data (via `yfinance`) using a virtual cash balance,
so a strategy can be developed and evaluated before anyone considers connecting
it to a real brokerage account.

## Layout

```
robinhood_bot/
  config.py            # runtime settings (symbols, timeframe, starting cash)
  data_feed.py          # pulls historical/live-ish price bars via yfinance
  paper_broker.py        # simulated broker: tracks cash, positions, fills orders
  portfolio.py            # position/equity accounting
  strategies/
    base.py               # Strategy interface
    moving_average.py      # SMA crossover example strategy
  runner.py                # wires data feed -> strategy -> paper broker, loops
  main.py                   # CLI entry point
tests/
  test_moving_average.py
data/                        # cached price data / trade logs (gitignored)
```

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python -m robinhood_bot.main --symbol AAPL --fast 10 --slow 30
```

## Status

Simulation only. There is no code here that places real orders or touches a
real brokerage account/credentials. If you later want to connect to a real
Robinhood account, that's a separate, deliberate step (unofficial API,
credential handling, MFA) — not something this scaffold does automatically.
