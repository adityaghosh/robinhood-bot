# Using robinhood-bot

An LLM-assisted short-term trading bot for a curated universe of
large-cap, liquid stocks (sourced from a saved Robinhood scan, ranked by
% change and RSI, gated by revenue growth) plus leveraged broad-market
index funds (TQQQ, UPRO) — no leveraged sector funds — pursuing a
monthly return target. Claude Code is the
decision-making agent; Python
enforces every hard risk limit and Claude cannot override a rejected
trade. Starts in paper mode (simulated fills against real, live prices)
with a one-line switch to live trading once trusted.

Background and full design rationale live in `docs/superpowers/specs/`
and `docs/superpowers/plans/` if you want the "why," not just the "how."

## One-time setup

**1. Python environment**

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

**2. Connect Robinhood's Agentic Trading MCP server**

The bot's price data and (in live mode) order execution both go through
Robinhood's official Agentic Trading MCP. This is a one-time connection
you set up yourself, outside this repo:

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

This opens a browser to authorize via the Robinhood app. Requires
Robinhood Gold. You'll fund a dedicated Agentic account separately from
your main brokerage account — that's the only account this bot can ever
touch, in either mode.

Both skills are wired to these confirmed MCP tool names: `get_accounts`
(resolve the dedicated Agentic account by `agentic_allowed: true`),
`get_equity_quotes` and `get_equity_historicals` (pricing/indicators, in
both modes), and, live-mode order execution only: `get_equity_tradability`
(pick a currently-open session), `get_equity_price_book` (marketable
limit price from the bid/ask), `place_equity_order`, and
`get_equity_orders` (confirm the actual fill before recording it — never
`review_equity_order`, which the skills deliberately skip since
`cli.py risk-check` is already the non-overridable gate). If Robinhood
renames or changes the shape of any of these, update
`.claude/skills/robinhood-trading/SKILL.md` /
`.claude/skills/robinhood-stop-loss-sweep/SKILL.md` to match.

**3. Confirm you're in paper mode**

```bash
python -m robinhood_bot.cli state --prices-json "{}"
```

Check `"trading_mode"` in the output — it should read `"paper"`. This is
the default and you should stay here until you trust the strategy.

## Running the bot day to day

Two Claude Code skills drive everything. Both are invoked manually for
now (no scheduled routine is set up yet).

**`/robinhood-trading`** — run once per trading day, after market close.
Reads current holdings, pulls a ranked candidate universe, researches a
shortlist, proposes trades, and gates every one through the hard risk
limits before executing. This is the skill that does the actual
research and decision-making.

**`/robinhood-stop-loss-sweep`** — run once at a second, fixed point in
the trading day (e.g. midday). Purely mechanical: checks open positions
against their stop-loss/profit-target thresholds and exits anything that
breached one. No research, no judgment calls — safe to run unattended
between full cycles.

Both skills print a plain-English summary of what they did (or didn't
do, and why) at the end — that's what you should actually read each day.
Full skill text: `.claude/skills/robinhood-trading/SKILL.md` and
`.claude/skills/robinhood-stop-loss-sweep/SKILL.md`.

### Automating the cadence

Right now you type `/robinhood-trading` and `/robinhood-stop-loss-sweep`
yourself at the right times. To have Claude Code fire them on a
schedule instead of by hand:

- The **`schedule`** skill sets up a cron-based cloud agent — the closest
  fit for "run `/robinhood-trading` every trading day after close" and
  "run `/robinhood-stop-loss-sweep` every trading day at midday," each on
  its own recurring cadence.
- The **`/loop`** command re-runs a prompt or slash command on an
  interval within a single session (e.g. `/loop 30m
  /robinhood-stop-loss-sweep`) — lighter-weight, but only lasts as long
  as that session stays open, so it suits a supervised trial run more
  than unattended production use.

Neither is wired up yet — this is still a manual, one-invocation-at-a-time
setup. Worth doing only once you've watched a few manual cycles in paper
mode and trust the timing.

## Backtesting against historical data

Before trusting the strategy with real (or even paper) money going
forward, validate it against the past. Backtesting reuses the exact same
ledger/risk-check/record-fill machinery as live/paper trading — it's
"paper trading against history," not a separate system — so a strategy
that works in a backtest is exercising the real risk engine, not a
simplified stand-in.

Two modes, both under `cli.py backtest ...`:

**Deterministic backtest** — a fast, fully-automated Python loop with no
LLM involvement. Can run months or years of history in seconds (after
the first, cold-cache run for a given date range and symbol set — see
"Where your data lives" below).

```bash
# See which dates it'll actually simulate (weekends/holidays excluded,
# derived from SPY's own trading history).
python -m robinhood_bot.cli backtest trading-days --start 2026-01-01 --end 2026-06-30

# Run it. Each --run id gets its own isolated ledger, so you can run
# multiple backtests side by side without them interfering. --candidates-json
# is required -- a JSON array of {"symbol": ..., "sector": ... or null} -- the
# command no longer fetches a candidate list itself (see below).
python -m robinhood_bot.cli backtest run --run jan-jun-2026 --start 2026-01-01 --end 2026-06-30 --candidates-json '[{"symbol": "AAPL", "sector": "Electronic Technology"}, {"symbol": "TQQQ", "sector": null}]'

# Summarize: total return, max drawdown, win/loss count, and a
# buy-and-hold-SPY benchmark for the same window.
python -m robinhood_bot.cli backtest report --run jan-jun-2026
```

`backtest run` takes whatever candidate list you pass it and applies it
retroactively across the whole historical window — not the universe as
it actually existed on each past date. This is a known, accepted
simplification (survivorship bias), not a bug; see
`docs/superpowers/specs/2026-07-19-backtesting-design.md` for the full
rationale and other non-goals. In practice that list is usually a
snapshot of today's live candidates — e.g. run the `universe rank`/
`universe finalize` sequence described under "Manual CLI reference"
below once and reuse its output — but the command itself is now
network-free and has no opinion on how you produced it.

**LLM-driven backtest** — runs the actual daily-cycle skill's research
and decision logic (the same judgment `/robinhood-trading` uses live),
day by day, over historical data instead of live Robinhood MCP quotes.
No MCP connection needed for this mode. Costs one reasoning pass per
simulated day, so use a realistically short window (weeks, not years):

```
/robinhood-trading --backtest --run RUN_ID --start 2026-01-01 --end 2026-01-31
```

Full step-by-step mapping from the live daily cycle to its backtest
equivalents: the "Backtest Mode" section of
`.claude/skills/robinhood-trading/SKILL.md`.

Both modes finish with `cli.py backtest report --run RUN_ID`.

## Switching to live trading

Edit `TRADING_MODE = "paper"` to `TRADING_MODE = "live"` in
`robinhood_bot/cli.py`. That's the entire switch — both skills read this
value programmatically at the start of every run via `cli.py state`, so
their behavior updates automatically. In live mode, trades execute for
real against your funded Agentic account.

**Do this only once you've watched the bot run in paper mode for a
while and are comfortable with its decisions.** There is no dry-run
confirmation step once you flip this — the next skill invocation will
place real orders.

## The risk limits (Python-enforced, not Claude's judgment)

Defined in `robinhood_bot/risk_engine.py`, defaults in `RiskConfig`:

| Limit | Default | What it does |
|---|---|---|
| Max active positions | 10 | Hard cap on concurrently held short-term slots |
| Stop-loss | 5% | Loss threshold that starts a position's grace period |
| Profit target | 8% | Gain threshold that triggers an automatic sell |
| Grace period | 5 days | How long an underwater position waits before parking |
| Max position size | 20% of equity | Ceiling on any single new position, scaled down as the long-hold bucket fills up (min 5%) |
| Long-hold capital cap | 30% of equity | Utilization threshold that drives the position-size scaling above |
| Monthly circuit breaker | 5% drawdown | Halts all new buys for the rest of the month if tripped |
| Profit banking | Starts at $250/week, +25%/$100 band | Permanently protects a growing share of realized gains beyond the weekly profit goal into non-tradeable `banked_cash` |

A position that breaches its stop-loss isn't sold immediately — it gets
a grace period to recover (`WAITING` status, still occupies an active
slot). If it doesn't recover in time, it's promoted to `LONG_HOLD`
(frees the slot, excluded from the monthly return goal, reviewed on a
slower cadence). These are config defaults, not final numbers — tune
them before trusting live mode with real money.

Every proposed trade — even in a skill session where Claude is
confident — goes through `cli.py risk-check` before execution. A
rejection is final; nothing in this system lets Claude override it.

## Manual CLI reference

Useful for debugging, or driving a step by hand instead of through a
skill. All commands print JSON to stdout.

```bash
# Current portfolio: cash, positions, monthly progress, trading_mode.
# Pass a JSON object of {"SYMBOL": price} for accurate equity, or "{}"
# to just check holdings/mode (positions come back marked stale).
python -m robinhood_bot.cli state --prices-json "{}"

# Ranked candidate universe: large-cap + liquid stocks from a saved
# Robinhood scan, ranked by a blend of % change and RSI, gated by a
# revenue-growth filter. Always real-time -- no cache, no --refresh.
# Both commands are network-free; the caller (the daily-cycle skill,
# manually, or a script) supplies the scan/financials/historicals data.
python -m robinhood_bot.cli universe rank --scan-rows-json '[...]'
python -m robinhood_bot.cli universe finalize --candidates-json '[...]' --closes-json '{...}'

# Ask whether a trade would be allowed, without executing it.
python -m robinhood_bot.cli risk-check buy AAPL --value 1500 --prices-json "{\"AAPL\": 189.50}"
python -m robinhood_bot.cli risk-check sell AAPL --prices-json "{\"AAPL\": 189.50}"

# Record a fill that already happened (simulated in paper mode, or
# mirrored from a real Robinhood order in live mode).
python -m robinhood_bot.cli record-fill buy AAPL --qty 5 --price 189.50 --reason "shortlist pick"
python -m robinhood_bot.cli record-fill sell AAPL --qty 5 --price 195.00 --reason "profit target"

# Check open positions against stop-loss/profit-target thresholds.
# Without --apply, this is a dry run (nothing is written to the ledger).
python -m robinhood_bot.cli check-stop-losses --prices-json "{\"AAPL\": 179.00}" --apply
```

**On Windows PowerShell**, native commands strip inner double quotes
from arguments — any non-empty `--prices-json` value needs its inner
quotes backslash-escaped, as shown above. `--prices-json "{}"` has no
inner quotes to strip, so it works as-is.

## Where your data lives

Everything under `data/` is gitignored — it's your local trading
history, not something the repo tracks:

- `data/ledger.json` — current portfolio state (cash, positions, month).
- `data/trade_log.csv` — append-only audit trail of every fill.
- `data/backtests/<run_id>/` — one isolated `ledger.json`, `trade_log.csv`,
  and `equity_curve.csv` per backtest run, keyed by the `--run` id you
  chose. Never touches the live `data/ledger.json`.
- `data/historical_price_cache/` — shared, run-independent OHLC cache
  (one file per symbol). This is what makes a second backtest over an
  overlapping date range fast — delete it if you ever need a clean
  re-fetch from `yfinance`.

## Current status

- Core engine, universe ranking, both skills, and backtesting are built
  and tested (`pytest` — all local/network-free; run `pytest -q` for the
  current count). Universe building sources live data from a Robinhood
  scan (see `docs/superpowers/specs/2026-07-21-scan-based-universe-design.md`)
  instead of the yfinance/Wikipedia scrape the original design used —
  `universe_client.py` now only contains `LiveHistoricalDataFetcher`,
  used solely by backtesting.
- Robinhood's Agentic Trading MCP is connected, and both skills are
  wired to its confirmed tool names for account resolution, quotes/
  historicals, and live-mode order placement/fill-confirmation (see
  "Connect Robinhood's Agentic Trading MCP server" above).
- **Not yet done:** a first manual paper-mode run to validate the whole
  loop end to end, and scheduled/automated invocation (see "Automating
  the cadence" above — both skills are still manual-only). See
  `docs/superpowers/plans/` for what's deliberately deferred and why.
