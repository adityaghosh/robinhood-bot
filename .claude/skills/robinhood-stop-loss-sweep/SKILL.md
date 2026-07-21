---
name: robinhood-stop-loss-sweep
description: Mechanical intraday safety-net sweep for the Robinhood trading bot — checks open positions against the stop-loss/grace-period rule and the weekly profit-goal tiers, and exits any that breach or clear them. No research, no discretion. Invoke at a fixed point mid-trading-day, between daily-cycle runs.
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

Call `get_equity_quotes` with every symbol from Step 1's
`active_positions` **and** `long_hold_positions` to fetch a current
price for each.

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

This sweep runs mid-day, so unlike the daily cycle it may find regular
hours still open — check `get_equity_tradability` per symbol (below)
rather than assuming an after-hours session.

For each SELL, in order (resolve and confirm one fully before moving to
the next):

1. **Resolve the account** (once per sweep, reuse for every SELL below):
   call `get_accounts`, filter for `agentic_allowed: true`. Exactly one
   such account should exist (this bot's dedicated Agentic account per
   `USAGE.md`) — if you find zero or more than one, stop and report
   instead of guessing.
2. **Check tradability and pick the session:** call
   `get_equity_tradability` for `account_number` + this symbol.
   - Eligible for `regular_hours` right now → use `regular_hours`.
   - Not eligible for `regular_hours` but eligible for `extended_hours`
     → use `extended_hours`; eligible only for `all_day_hours` → use
     that instead.
   - Eligible for none of those right now → skip this SELL this sweep
     and note it in the report as "not currently tradable in any live
     session."
3. **Get the marketable limit price:** call `get_equity_price_book` for
   this symbol and use the best **bid** (this is a SELL) as
   `limit_price` — never the Step 2 quote, which may be stale by now.
4. **Place the order:** call `place_equity_order` directly (no
   `review_equity_order` first — `trading_mode: "live"` plus this
   position already having breached its threshold via
   `check-stop-losses` is this sweep's standing authorization) with
   `account_number`, `symbol`, `side: "sell"`, `type: "limit"`,
   `limit_price` (as a string) from step 3, `quantity` (as a string) =
   `<held qty>`, `market_hours` from step 2, and a freshly generated
   `ref_id`. If it fails or is rejected: do not call `record-fill`;
   report the failure and reason plainly.
5. **Confirm the fill:** call `get_equity_orders` with `account_number`
   and the returned `order_id`. On `filled`, use the actual filled
   quantity and average price for `record-fill` below. On
   `partially_filled`, check once or twice more; if still partial, only
   record the quantity actually filled. On `cancelled`/`rejected`/
   `failed`/`voided`, do not call `record-fill` — report the terminal
   state. If still pending after a few checks, treat as unresolved this
   sweep and say so, rather than guessing at a fill.

```
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <actual filled qty> --price <actual average fill price> --reason "weekly profit-goal exit"
```

Entries with `"action": "SKIP"` or `"action": "HOLD"` need no action.

## Step 5 — Report

One or two lines: what (if anything) was sold and why, and the current
cash/active-position count after this sweep. No further analysis —
that's the daily cycle's job, not this one.
