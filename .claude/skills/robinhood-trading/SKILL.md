---
name: robinhood-trading
description: Run the daily research-and-trade cycle for the Robinhood paper/live trading bot — fetches ranked candidates, researches a shortlist, gates every trade through cli.py risk-check, and records fills. Invoke once per trading day, after market close.
---

# Daily Trading Cycle

Run this once per trading day, after market close. It reads current
holdings and mode, gets a ranked candidate universe, researches a
shortlist, proposes trades, gates every trade through the hard risk
limits in `risk_engine.py`, and executes.

All `cli.py` commands below assume the project's virtualenv is active
(`python -m robinhood_bot.cli ...`). If it isn't, activate it first
(`.venv\Scripts\activate` on Windows).

On Windows PowerShell, native commands strip inner double quotes, so any
non-empty `--prices-json` value needs its inner quotes backslash-escaped,
e.g. `--prices-json '{\"AAPL\": 189.50, \"MSFT\": 310.25}'`. The empty
`--prices-json "{}"` used in Step 1 has no inner quotes to strip, so it
works as-is.

## Step 1 — Read mode & current holdings

```
python -m robinhood_bot.cli state --prices-json "{}"
```

Prices come back marked `stale_price: true` here — that's expected and
fine, this call is only to learn:
- `trading_mode`: `"paper"` or `"live"`. **This governs everything below.**
  Never call the live order-placement MCP tool while this is `"paper"`.
- The symbols currently in `active_positions` and `long_hold_positions`.
- Current `month_start_equity` and `monthly_return_pct`, for context on
  progress toward this month's return goal.

## Step 2 — Get the ranked universe

```
python -m robinhood_bot.cli universe
```

This uses a weekly-cached membership list by default (fast). Only pass
`--refresh` if explicitly asked to force a refresh.

## Step 3 — Build today's research shortlist

From the `candidates` list (sorted by `combined_rank`, descending), take:
- The top 15 candidates whose `category` is `"sp500"` or `"nasdaq100"`.
- All candidates whose `category` is `"leveraged"` (there are only 3 —
  TQQQ, UPRO, SOXL — so this just means include all of them).
- Every symbol from Step 1's `active_positions` and `long_hold_positions`
  that isn't already in the shortlist, so open positions are always
  reconsidered even if they've fallen out of the top rankings.

## Step 4 — Get fresh quotes

Using the Robinhood MCP quote tool (e.g. `get_equity_quotes`), fetch a
current price for every symbol in the Step 3 shortlist.

**If a quote fails for any symbol: skip that symbol for this cycle.**
Never fabricate, estimate, or reuse a stale price in its place.

## Step 5 — Refresh state with real prices

```
python -m robinhood_bot.cli state --prices-json "<fresh quotes from Step 4, as a JSON object of symbol: price>"
```

Now `total_equity`, `unrealized_pnl_pct`, and `monthly_return_pct` are
accurate, not placeholder values.

## Step 6 — Research and decide, per shortlisted symbol

For each symbol currently **held** (active or long-hold):
- Note its lifecycle `status` (`ACTIVE`, `WAITING`, `LONG_HOLD`) and
  `unrealized_pnl_pct` from Step 5.
- `LONG_HOLD` positions are not part of today's short-term rotation —
  only consider selling one if it has clearly recovered and you'd
  exit it; otherwise leave it alone.
- For `ACTIVE`/`WAITING` positions, decide: propose **SELL** (if you'd
  exit today) or **HOLD** (do nothing).

For each shortlisted symbol **not currently held**:
- Consider its `combined_rank` (volatility), `realized_vol`/`atr_pct`,
  and recent price action from the fresh quote.
- Decide: propose **BUY** (a new position) or skip it.
- You can open at most as many new positions as there are free slots
  out of the 5-slot active cap (`5 - len(active_positions)` from
  Step 5's `active_positions`, since `WAITING` positions still occupy a
  slot).

## Step 7 — Gate every proposed BUY/SELL

For every proposed trade, before doing anything else, run its risk-check
and then execute it (Step 8) before moving to the next proposed trade.
Don't batch all of a cycle's risk-checks ahead of execution — the
slot-cap check reads live ledger state, so each buy's risk-check must
run immediately before that specific buy executes, reflecting any buys
already executed earlier in this same cycle.

```
python -m robinhood_bot.cli risk-check buy SYMBOL --value <proposed dollar amount> --prices-json "<fresh quotes>"
python -m robinhood_bot.cli risk-check sell SYMBOL --prices-json "<fresh quotes>"
```

- If `"approved": false`, **do not execute this trade.** Read `"reason"`
  and either propose a smaller size / different symbol, or fall back to
  HOLD. Never override a rejection.
- For an approved BUY, `"max_position_value"` is the ceiling. Compute a
  whole-share quantity: `floor(min(proposed_value, max_position_value) /
  fresh_quote_price)`. You may propose fewer shares than the ceiling
  allows.

## Step 8 — Execute approved trades

**If `trading_mode` is `"paper"`:**

```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote price> --reason "<why>"
```

Never call the live order-placement MCP tool in this mode.

**If `trading_mode` is `"live"`:**

1. Call the Robinhood MCP order-placement tool (e.g.
   `place_equity_order`) for the approved trade.
2. Once it confirms a fill, call `record-fill` using the **actual**
   filled quantity and price from that tool's response — never the
   pre-trade quote, even if they're close.

```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual fill price> --reason "<why>"
```

If order placement fails: do not call `record-fill`. Leave the ledger
untouched and note the failure in your summary — surface it, don't
retry silently or guess at what happened.

## Step 9 — Summarize

Run `python -m robinhood_bot.cli state --prices-json "<fresh quotes>"`
one more time and report to the user:
- What trades were made today and why (or why none were made).
- Current `monthly_return_pct` against the monthly goal.
- Anything that failed or was skipped (a quote that didn't come back, an
  order that failed to place), stated plainly.

## Backtest Mode

Invoked explicitly with a date range and run id, e.g. `/robinhood-trading
--backtest --run RUN_ID --start 2026-01-01 --end 2026-03-31`. Runs the same
research-and-decide loop as the daily cycle above, once per simulated
trading day, entirely against historical data — no Robinhood MCP
connection is used or needed in this mode.

### Get the list of simulated days

```
python -m robinhood_bot.cli backtest trading-days --start START_DATE --end END_DATE
```

Loop through each date in the returned `trading_days` list, in order,
running Steps 1-9 below for each one before moving to the next simulated
date.

### Per-simulated-day steps

Replace the live commands from the daily cycle above with their `backtest`
equivalents, all parameterized by `--run RUN_ID --asof <simulated date>`:

- **Step 1 (read mode & holdings):** `python -m robinhood_bot.cli backtest
  state --run RUN_ID --asof <simulated date> --prices-json "{}"`. Note that
  `trading_mode` here is always `"backtest"` — there is no live-order-
  placement branch anywhere in this mode; every trade is simulated.
- **Step 2 (universe):** skipped — `backtest run`'s candidate list (today's
  live universe, applied retroactively) isn't available per-command in
  this mode. Instead, shortlist from whatever symbols you already know are
  liquid, well-known equities (e.g. run `cli.py universe` once, live,
  before starting the backtest, and reuse that fixed candidate list for
  every simulated day — mirroring exactly what `backtest run`'s
  deterministic mode does internally).
- **Step 4 (fresh quotes):** `python -m robinhood_bot.cli backtest quote
  SYMBOL --asof <simulated date>` for each shortlisted symbol, in place of
  the Robinhood MCP quote tool. If `"price"` comes back `null`, skip that
  symbol for this simulated day — same rule as a failed live quote.
- **Step 5 (refresh state with real prices):** `python -m robinhood_bot.cli
  backtest state --run RUN_ID --asof <simulated date> --prices-json
  "<quotes from Step 4>"`.
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --prices-json "<quotes>"`, then on approval, `python -m robinhood_bot.cli
  backtest record-fill {buy|sell} SYMBOL --run RUN_ID --asof <simulated
  date> --qty <n> --price <quote price> --reason "<why>"`. There is no
  live-order-placement call in this mode, ever.

### Summarize

After the last simulated day, run:

```
python -m robinhood_bot.cli backtest report --run RUN_ID
```

Report `total_return_pct`, `max_drawdown_pct`, `wins`/`losses`, and
`benchmark_return_pct` (buy-and-hold SPY over the same window) to the
user, alongside any symbols that were skipped for missing quotes.
