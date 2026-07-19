# Robinhood Agentic Monthly Trading Bot — Design

Status: Approved for planning
Date: 2026-07-18

## Purpose

Extend the existing paper-trading scaffold into an LLM-assisted, short-term
trading system that pursues a fixed monthly profit-return goal, restricted to
a curated universe of well-established, liquid tickers. Claude Code is the
decision-making agent, using Robinhood's official Agentic Trading MCP server
for market data and (eventually) order execution, with hard risk limits
enforced by deterministic Python code that Claude cannot override.

Start in paper mode (simulated fills using real, live market data) with a
config-level switch to live trading once the strategy is trusted.

## Background: Robinhood Agentic Trading MCP

Robinhood launched "Agentic Trading" on 2026-05-27: a hosted MCP server
(`https://agent.robinhood.com/mcp/trading`) that AI agents (Claude Code,
Claude Desktop, others) connect to directly to read portfolio/quote data and
place equity orders in a dedicated "Agentic account" (separate from the
user's main brokerage account, pre-funded, capped). Confirmed tools include
`get_accounts`, `get_portfolio`, `get_equity_positions`, `get_equity_quotes`,
`get_equity_orders`, `place_equity_order`, `cancel_equity_order`. Requires
Robinhood Gold. Equities only at launch (options/crypto/futures on roadmap).

**Important limitation:** there is no native paper-trading/sandbox mode.
Any call to `place_equity_order` is a real order against real (capped) funds.
Paper trading in this project is therefore something we simulate ourselves —
using the MCP's read-only tools for live prices, with our own local ledger
standing in for the broker.

Sources: [Robinhood Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/),
[TechCrunch, 2026-05-27](https://techcrunch.com/2026/05/27/robinhood-now-lets-your-ai-agents-trade-stocks/),
[Saving to Invest — 3 weeks with Claude trading](https://savingtoinvest.com/i-let-claude-trade-my-robinhood-agentic-account-heres-what-happened/)

## Goals

- Pursue a **fixed target monthly return %** via short-term trades (entries
  and exits can happen any day within the month), not buy-and-hold DCA.
- Restrict trading to a curated, dynamically-sourced universe: top ~100 S&P
  500 constituents by market cap, top ~20 Nasdaq-100 constituents, and
  leveraged full-index funds (e.g. TQQQ, SPXL, UPRO, SSO-style products).
- Within that universe, prioritize candidates with higher recent price
  volatility (wider recent trading range), since that widens the achievable
  profit window for a short-term strategy.
- Hold at most 5 active short-term positions at a time.
- Take current holdings into account on every decision cycle, not just fresh
  picks in isolation.
- Enforce hard, non-LLM-overridable risk limits: per-position stop-loss /
  profit-target, a grace period for underwater positions before parking them,
  and a monthly circuit breaker.
- Runnable and testable locally today in paper mode; runnable later as a
  scheduled Claude routine with a one-line config switch to live trading.

## Non-goals (for this spec)

- Options, crypto, or futures trading (not yet supported by Robinhood's MCP).
- Exact numeric parameter tuning (stop-loss %, profit-target %, grace period
  length, circuit-breaker %, position sizing %, initial/monthly capital
  amounts) — these are config defaults to be tuned together before any live
  trading, not architectural decisions.
- A UI/dashboard. Output is CLI + a plain audit log (`data/trade_log.csv`).

## Architecture

Claude Code is the daily decision-making agent — not a Python script calling
the Anthropic API. In each session it has two tool sources: Robinhood's MCP
server (research + execution) and this repo's local CLI (state + hard risk
limits). Python owns everything deterministic; Claude owns research and
ticker selection within the limits Python enforces.

```
robinhood_bot/
  config.py              # TRADING_MODE (paper|live), risk limit defaults, cadence
  universe.py             # dynamic universe fetch: S&P100 + Nasdaq20 + leveraged
                           # funds, ranked/filtered by recent realized volatility
  portfolio_state.py       # read/write local ledger: active positions (<=5),
                           # long-hold bucket, cash, entry price/date, days-held
  risk_engine.py            # pure functions: position sizing (incl. long-hold
                             # utilization scaling), stop-loss/target checks,
                             # long-hold promotion rule, monthly circuit breaker
  paper_broker.py            # (existing, extended) simulated fills using
                              # MCP-sourced live prices
  ledger.py                   # persistence for portfolio_state (data/, gitignored)
  cli.py                       # Claude-facing commands: state, universe,
                                # risk-check, record-fill, check-stop-losses
.claude/skills/robinhood-trading/
  SKILL.md                      # daily decision-cycle procedure
.claude/skills/robinhood-stop-loss-sweep/
  SKILL.md                       # mechanical intraday safety-net procedure
data/
  ledger.json                     # position/cash state (gitignored)
  trade_log.csv                    # append-only audit trail (gitignored)
tests/
  test_risk_engine.py
  test_universe.py
  test_portfolio_state.py
```

`risk_engine.py` is deliberately dumb: deterministic, no I/O beyond reading
values it's given, no LLM involvement. It is the thing that can't be argued
with mid-session.

## Daily Decision Cycle (main routine, once daily post-close)

1. `cli.py state` → active positions (≤5), long-hold bucket, cash, entry
   price/date, days-held, unrealized P&L, and progress toward the monthly
   return goal.
2. `cli.py universe` → dynamic universe list annotated with a recent
   realized-volatility metric (e.g. 20-day realized vol / ATR%).
3. Claude researches candidates and current holdings via Robinhood MCP tools
   (quotes, historicals, news) and the volatility ranking, and proposes one
   action per relevant symbol: `BUY`, `SELL`, or `HOLD`.
4. Every proposed `BUY`/`SELL` must pass `cli.py risk-check` before
   execution. This enforces: 5-slot cap, position sizing (scaled down as
   long-hold utilization rises — see Risk Limits below), stop-loss/target
   thresholds, the grace-period/long-hold rule, and the monthly circuit
   breaker. A rejected trade is not placed; Claude may propose an
   alternative or hold.
5. Execution:
   - Paper mode: `cli.py record-fill` simulates the fill in the local ledger
     using the live MCP-sourced quote price. Claude never calls
     `place_equity_order` in paper mode.
   - Live mode: Claude calls the real `place_equity_order` MCP tool, then
     `cli.py record-fill --confirm` mirrors the resulting fill into the same
     ledger, so paper and live produce the same audit trail shape.

## Intraday Stop-Loss Safety Net (second, minimal routine)

Runs at a second, fixed time each trading day (e.g. midday). This is a
second, much smaller Claude Code routine running a strictly mechanical
skill — no research, no discretion. It calls `cli.py check-stop-losses`
(pure Python rule evaluated against the ledger plus a fresh MCP quote per
active/waiting position) and executes only what that command returns: an
exit via `place_equity_order` (live) or `record-fill` (paper) if a hard
threshold is breached, otherwise nothing.

This reuses the same MCP connection/auth Claude already has for the daily
cycle rather than having Python hold independent Robinhood credentials —
avoids building and maintaining a second credential/session path.

## Risk Limits & Position Lifecycle

Each position moves through a small state machine tracked in the ledger:

```
ACTIVE (short-term slot, 1 of 5 max)
  - price >= entry * (1 + profit_target_pct)         -> SELL, slot freed
  - price in [stop-loss threshold, profit target)      -> stays ACTIVE
  - price < entry * (1 - stop_loss_pct):
      - within grace_period_days                        -> WAITING (still
        occupies 1 of 5 slots, watched daily)
      - grace_period_days exceeded, no recovery           -> promoted to
        LONG_HOLD (frees the slot)
LONG_HOLD:
  - excluded from the 5-slot cap and from daily short-term rotation
  - reviewed on a slower cadence (e.g. weekly) for an eventual exit
  - not subject to the monthly return goal
```

`risk_engine.py` owns all thresholds as pure functions: `stop_loss_pct`,
`profit_target_pct`, `grace_period_days`, `max_position_pct`, and the
**monthly circuit breaker** — if realized + unrealized portfolio drawdown
for the month exceeds a configured %, all new `BUY`s are rejected for the
rest of the month (existing positions can still be managed/exited).

**Long-hold utilization scaling.** Because LONG_HOLD sits outside the
5-slot cap, total capital at risk isn't bounded by the slot count alone. To
prevent unbounded exposure growth without a hard cutoff, `risk_engine`
computes long-hold utilization (long-hold capital ÷ a configured long-hold
capital cap) and uses it to scale down the max position size allowed for new
`BUY`s as utilization rises: full sizing at 0% utilization, progressively
more conservative sizing as long-hold fills up, with a floor/reject once
utilization reaches its cap. This makes the bot more conservative about
opening new active positions precisely when it's already carrying parked
losers, without a binary "no more trading" cliff.

All numeric thresholds above are config defaults, tuned together before any
live trading.

## Universe Selection

`universe.py` fetches, at each cycle, the current top ~100 S&P 500
constituents and top ~20 Nasdaq-100 constituents by market cap, plus a
fixed/maintained list of leveraged full-index funds. Each candidate is
annotated with a recent realized-volatility metric sourced from historical
price data (via Robinhood MCP or a market-data library), and the list is
ranked/filterable by that metric so Claude can prioritize higher-volatility
names within the approved universe — this directly targets a wider
short-term profit window without expanding the universe beyond well-
established, liquid names.

## Paper/Live Switch

A single `config.py` setting: `TRADING_MODE = "paper" | "live"`, read at the
start of every CLI invocation and referenced explicitly in both SKILL.md
procedures so Claude's instructions branch on mode — Claude is never told to
call `place_equity_order` while in paper mode. Switching modes is a one-line
config change, not a code change.

## Data Source

Robinhood's MCP read-only tools (`get_equity_quotes`, `get_equity_positions`,
etc.) are the price source for both paper and live mode — one data source in
both modes, no paper/live price discrepancy, and Claude already has this
connection available regardless of mode. (The existing `yfinance`-based
`data_feed.py` from the original scaffold is superseded for this feature;
it may still be useful later for historical backtesting, out of scope here.)

## Testing Strategy

- **Unit tests (`pytest`, no network):** `risk_engine.py` state-machine
  transitions and thresholds (including long-hold utilization scaling),
  `universe.py` filtering/ranking logic, `portfolio_state.py` ledger
  read/write correctness.
- **Manual end-to-end (local, now):** run the SKILL.md daily-cycle procedure
  yourself in a Claude Code session against this repo, in paper mode, with
  the real Robinhood MCP connection active for read-only quotes — so paper
  mode uses genuinely live data, only order placement is simulated.

## Error Handling

- Any MCP read failure (quote, historical, news) for a given symbol: skip
  that symbol for the cycle. Never fabricate or estimate a price.
- Any order-placement failure (live mode): leave the ledger unchanged, log
  the failure, surface it at the start of the next session. Never guess at
  a fill.
- `risk-check` fails closed: if required state is missing or ambiguous, the
  trade is rejected, not approved by default.

## Open Items for Follow-up (not blocking this spec)

- Exact values: stop_loss_pct, profit_target_pct, grace_period_days,
  max_position_pct, long-hold capital cap, monthly circuit-breaker %,
  initial capital, and fixed monthly contribution amount.
- This directory is not yet a git repository — needs `git init` (or the
  user's preferred VCS setup) before design docs / code here can be
  version-controlled and committed.
