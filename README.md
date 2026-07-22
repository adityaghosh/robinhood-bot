# robinhood-bot

An LLM-assisted short-term trading bot for a curated universe of
large-cap, liquid stocks (sourced from a saved Robinhood scan, ranked by
% change and RSI, gated by revenue growth) plus leveraged broad-market
index funds (TQQQ, UPRO), pursuing a monthly return target. Claude Code
is the decision-making agent; Python enforces every hard risk limit and
Claude cannot override a rejected trade. Starts in paper mode (simulated
fills against real, live prices) with a one-line switch to live trading
once trusted.

See **[USAGE.md](USAGE.md)** for setup, the daily-cycle/stop-loss-sweep
skills, backtesting, the risk limits, and everything else needed to
actually run this.

## Layout

```
robinhood_bot/
  cli.py                  # network-free CLI: state, risk-check, record-fill,
                           # check-stop-losses, universe rank/finalize, backtest ...
  commands.py              # cli.py's command implementations
  risk_engine.py            # hard risk limits (stop-loss, position sizing,
                             # sector concentration, profit banking, ...)
  universe.py                # candidate ranking (percentile-rank math,
                              # MA-trend/golden-cross attachment)
  universe_client.py          # LiveHistoricalDataFetcher (yfinance, backtesting only)
  ledger.py                    # portfolio state persistence
  portfolio_state.py            # Position/PortfolioState data model
  backtest_commands.py           # deterministic + LLM-driven backtest support
  backtest_data.py                # historical price cache for backtesting
.claude/skills/
  robinhood-trading/               # daily research-and-trade cycle skill
  robinhood-stop-loss-sweep/        # mechanical intraday safety-net sweep skill
docs/superpowers/
  specs/, plans/                     # design docs and implementation plans
tests/                                # pytest suite, network-free
data/                                  # local trading state (gitignored)
```

## Status

Paper mode by default — see USAGE.md's "Current status" section for what's
built, what's connected, and what's still manual.
