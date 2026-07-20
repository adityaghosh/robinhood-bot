---
name: robinhood-stop-loss-sweep
description: Mechanical intraday safety-net sweep for the Robinhood trading bot — checks open positions against hard stop-loss/profit-target thresholds and exits any that breach them. No research, no discretion. Invoke at a fixed point mid-trading-day, between daily-cycle runs.
---

# Stop-Loss Sweep

Run this at a fixed point during the trading day (e.g. midday), between
daily-cycle runs. This is deliberately mechanical: no research, no
judgment calls. Its only job is to catch a position that's breached a
hard threshold before the next full daily cycle would notice.

All `cli.py` commands below assume the project's virtualenv is active
(`python -m robinhood_bot.cli ...`).

On Windows PowerShell, native commands strip inner double quotes, so any
non-empty `--prices-json` value needs its inner quotes backslash-escaped,
e.g. `--prices-json '{\"AAPL\": 189.50, \"MSFT\": 310.25}'`. The empty
`--prices-json "{}"` used in Step 1 has no inner quotes to strip, so it
works as-is.

## Step 1 — Read current holdings

```
python -m robinhood_bot.cli state --prices-json "{}"
```

Note `trading_mode` and every symbol in `active_positions` (this
includes both `ACTIVE` and `WAITING` status positions — both occupy an
active slot and both are checked here) **and** every symbol in
`long_hold_positions` — a recovered long-hold position is now eligible
for the weekly profit-goal sweep below, exactly like an active winner.

## Step 2 — Get fresh quotes

Using the Robinhood MCP quote tool (e.g. `get_equity_quotes`), fetch a
current price for every symbol from Step 1's `active_positions` **and**
`long_hold_positions`.

**If a quote fails for any symbol: skip that symbol this sweep.** Never
fabricate or reuse a stale price.

## Step 3 — Run the stop-loss check

```
python -m robinhood_bot.cli check-stop-losses --prices-json "<fresh quotes>" --apply
```

This one command evaluates every active position's stop-loss/grace-
period state, **and** checks all active *and* long-hold positions
against this week's profit goal (`week_realized_pnl` vs.
`week_profit_target` from `state`) — the biggest winners get flagged for
sale first, escalating to the next tier once the current one is
cleared, rather than stopping for the rest of the week the moment the
goal is first hit. Returns a `results` list. Because `--apply` was
passed, any `PROMOTE_LONG_HOLD` result has **already been applied** to
the ledger — the position has moved from `active_positions` to
`long_hold_positions`. You don't need to do anything further for those.

## Step 4 — Execute any SELL results

For each entry in `results` where `"action": "SELL"`:

**If `trading_mode` is `"paper"`:**

```
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote from Step 2> --reason "weekly profit-goal exit"
```

`<held qty>` isn't in the `check-stop-losses` result — pull it from
Step 1's `active_positions` **or** `long_hold_positions` data for this
symbol, since a sold symbol could now be either.

**If `trading_mode` is `"live"`:**

1. Call the Robinhood MCP order-placement tool (e.g.
   `place_equity_order`) to sell the position.
2. Call `record-fill` using the actual filled quantity/price from that
   tool's response, not the Step 2 quote.

Entries with `"action": "SKIP"` or `"action": "HOLD"` need no action.

## Step 5 — Report

One or two lines: what (if anything) was sold and why, and the current
cash/active-position count after this sweep. No further analysis —
that's the daily cycle's job, not this one.
