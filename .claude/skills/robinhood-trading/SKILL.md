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

For every proposed trade, before doing anything else:

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
