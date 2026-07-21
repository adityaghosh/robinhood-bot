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
non-empty `--prices-json` or `--closes-json` value needs its inner quotes
backslash-escaped, e.g. `--prices-json '{\"AAPL\": 189.50, \"MSFT\":
310.25}'`. The empty `--prices-json "{}"` used in Step 1 has no inner
quotes to strip, so it works as-is.

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
- Current `week_realized_pnl` and `week_profit_target`, for context on
  how much room is left before this week's profit goal — useful when
  weighing whether to cut a lagging position in Step 6 below.
- `prior_week_realized_pnl` and `effective_max_active_positions` — a
  strong prior week can raise this cycle's active-slot cap above the
  usual 5 (see Step 6). `effective_max_active_positions` is the real
  cap to use everywhere below; the "5-slot" figure from before this
  mechanism existed is only the baseline, not a hard ceiling anymore.
- `cash` is tradeable cash only — it excludes `banked_cash`, a separate,
  permanently protected pool. Once a week's cumulative realized profit
  crosses `week_profit_target`'s underlying $500 threshold, each further
  dollar of gain is progressively banked (25% of the next $100, 50% of
  the $100 after that, and so on up to 100%) and moves into
  `banked_cash` instead of `cash` — a cushion so a later bad stretch
  can't claw back everything, including past winnings. `banked_cash`
  still counts toward `total_equity` (and thus `monthly_return_pct` and
  the circuit breaker) — it's protected from *trading*, not excluded
  from net worth. This is fully mechanical; there's no discretionary
  step for it.

## Step 2 — Get the ranked universe

Candidates come from a saved Robinhood scan (`scan_id`:
`2447fab8-9697-44b6-8d3f-78606d0f1e38`) rather than an S&P 500/Nasdaq-100
membership list — a deliberate shift from "large, well-established,
ranked by volatility" to "large, liquid, ranked by momentum + RSI, gated
by revenue growth." See
`docs/superpowers/specs/2026-07-21-scan-based-universe-design.md` for the
full rationale.

1. Call `run_scan` with the scan above. This is real-time data, not
   cached — call it fresh every cycle. Each returned row has a top-level
   `ticker` and a `columns` map keyed by display name; build `scan_rows`
   for the next step from `columns['Market cap']` → `market_cap`,
   `columns['% Change']` → `pct_change`, `columns['RSI']` → `rsi` (all
   three come back as strings — parse to float), and `ticker` (or
   `columns['Symbol']`) → `symbol`.
2. `python -m robinhood_bot.cli universe rank --scan-rows-json "<scan_rows
   from step 1, as a JSON array of {symbol, market_cap, pct_change,
   rsi}>"` — returns the full result set sorted descending by
   `combined_rank` (a percentile-rank average of `pct_change` and `rsi`,
   computed in Python, not by hand).
3. Walk that sorted list top-down. For each candidate, in batches of up to
   20 symbols (the `get_financials` per-call limit), call `get_financials`
   (`period: "quarterly"`, `limit: 5`) and compute YoY revenue growth:
   `(revenue_this_quarter - revenue_same_quarter_last_year) /
   revenue_same_quarter_last_year`. Drop any candidate with negative or flat
   growth. If `get_financials` fails for a candidate, drop it too (never
   assume a candidate passes a check that couldn't be verified) and keep
   walking. Stop once 20 survivors are collected or the list runs out.
4. Append the 2 leveraged funds (`TQQQ`, `UPRO`) unconditionally — they
   never go through the scan or the growth filter. Give each a fixed
   `combined_rank` of `0.5` and `sector: null`.
5. For all ~22 finalists: call `get_equity_historicals` (`interval: "day"`,
   `start_time` ~330 calendar days back — verified empirically that ~210
   calendar days only yields ~142 trading days, short of the 200 the
   golden-cross check needs; 330 calendar days yields ~220+, batched up to
   10 symbols/call) to build a `symbol: [chronological closes]` object —
   same pattern as Step 4 below uses for held positions. Also call
   `get_equity_fundamentals` (batched up to 10/call) for each finalist's
   `sector` (leveraged funds get `sector: null` directly, skip fetching
   fundamentals for them).
6. `python -m robinhood_bot.cli universe finalize --candidates-json "<22
   finalists: symbol, category ('scanned' or 'leveraged'), market_cap,
   pct_change, combined_rank, sector, rsi>" --closes-json "<historicals from
   step 5>"` — attaches `ma_trend_bullish`/`golden_cross_bullish` per
   candidate (`null` for any symbol whose historicals fetch failed or came
   back with fewer than 200 closes — omit that symbol from the closes
   object passed in, exactly like the held-position rule in Step 4 below).

**If `run_scan` fails or returns zero rows:** skip new BUY consideration for
this entire cycle and say so plainly in the Step 9 summary — there is no
fallback candidate source. Held-position management (Step 6's discretionary
calls, and the separate stop-loss-sweep skill) is unaffected, since neither
depends on the candidate universe.

Each candidate in the final list carries `sector` (needed in Step 7 when
gating a BUY), `rsi` (14-day RSI from the scan), `ma_trend_bullish` (5-day
vs. 20-day moving average), and `golden_cross_bullish` (50-day vs. 200-day)
— all three needed in Step 7, exactly as before.

## Step 3 — Build today's research shortlist

From the `candidates` list (sorted by `combined_rank`, descending), take:
- The top 15 candidates whose `category` is `"scanned"`.
- All candidates whose `category` is `"leveraged"` (there are only 2 —
  TQQQ, UPRO, both broad-market index funds, no leveraged sector funds —
  so this just means include all of them).
- Every symbol from Step 1's `active_positions` and `long_hold_positions`
  that isn't already in the shortlist, so open positions are always
  reconsidered even if they've fallen out of the top rankings.

## Step 4 — Get fresh quotes

Call `get_equity_quotes` with the Step 3 shortlist's symbols to fetch a
current price for each.

**If a quote fails for any symbol: skip that symbol for this cycle.**
Never fabricate, estimate, or reuse a stale price in its place.

Also, for every symbol in Step 1's `active_positions` and
`long_hold_positions` (already a subset of this shortlist per Step 3),
fetch daily closes via `get_equity_historicals` — `symbols` (up to 10 per
call, batch into multiple calls if more than 10 symbols are held),
`interval: "day"`, `start_time` set to ~330 calendar days back from today
(verified empirically that ~210 calendar days only yields ~142 trading
days, short of the 200 the golden-cross check needs; 330 calendar days
yields ~220+) as an RFC3339 UTC timestamp (e.g. `2026-01-01T00:00:00Z`;
`end_time` can be omitted, it defaults to now) — and build a JSON object
of `symbol:
[chronological closing prices, oldest first]` from each bar's
`close_price`. This replaces `cli.py`'s own (yfinance-based) lookup for
these symbols' RSI/moving-average/golden-cross figures with the same
source the fresh quotes just came from.

**If historicals fail for a held symbol, or come back with fewer than
200 closes: omit that symbol from the closes object.** `cli.py` falls
back to its own lookup only when the whole `--closes-json` argument is
empty, not per-symbol, so leaving out just the affected symbol means it
keeps this cycle's `rsi`/`ma_trend_bullish`/`golden_cross_bullish` at
their neutral defaults instead of silently using stale figures.

## Step 5 — Refresh state with real prices

```
python -m robinhood_bot.cli state --prices-json "<fresh quotes from Step 4, as a JSON object of symbol: price>" --closes-json "<closes object from Step 4, as a JSON object of symbol: [closes]>"
```

Now `total_equity`, `unrealized_pnl_pct`, and `monthly_return_pct` are
accurate, not placeholder values, and held positions' `rsi`,
`ma_trend_bullish`, and `golden_cross_bullish` (used in Step 6) come from
the same fresh data source as the quotes rather than a separate yfinance
lookup.

## Step 6 — Research and decide, per shortlisted symbol

Profit-taking is no longer a per-position judgment call here — it's
fully mechanical now, driven by the weekly profit goal
(`risk_engine.evaluate_profit_exits`, surfaced through
`check-stop-losses`'s `SELL` results, covered in the stop-loss-sweep
skill). Your discretion in this step is for two things instead:

For each symbol currently **held** (active or long-hold):
- Note its lifecycle `status` (`ACTIVE`, `WAITING`, `LONG_HOLD`),
  `unrealized_pnl_pct`, `rsi`, `ma_trend_bullish`, and
  `golden_cross_bullish` from Step 5.
- **ACTIVE/WAITING positions:** consider a discretionary early SELL if
  a position has moved sharply against you (you don't have to wait out
  the full grace period if the decline looks decisive rather than
  noisy — see this session's backtest transcripts for worked examples
  of both calls), or if RSI is deep in overbought territory, or if
  `ma_trend_bullish` has turned `false`.
- **LONG_HOLD positions:** these have no guaranteed recovery, so treat
  `ma_trend_bullish` turning `true` (a bounce back above the 20-day
  average) as a signal to consider **selling into the bounce** rather
  than holding out for a full recovery that may not come — this is
  often the best exit opportunity a long-hold position gets. A
  `golden_cross_bullish` flip to `true` (the 50-day average moving back
  above the 200-day average) is a **stronger, higher-conviction**
  version of the same signal — it's a slower-moving, more durable read
  than the 5/20 check, which can reverse within days in a choppy
  market. When both are `true` at once, that's the clearest case for
  selling into the bounce; a `ma_trend_bullish`-only flip is a weaker,
  more provisional read worth weighing against how deep the position is
  underwater.
- Otherwise, propose **HOLD** — the mechanical stop-loss/grace-period
  machinery and the weekly profit-goal sweep both run independently of
  this step and will catch what they're each designed to catch.

For each shortlisted symbol **not currently held**:
- Consider its `combined_rank` (volatility), `realized_vol`/`atr_pct`,
  and recent price action from the fresh quote.
- Decide: propose **BUY** (a new position) or skip it.
- You can open at most as many new positions as there are free slots
  out of the active cap (`effective_max_active_positions -
  len(active_positions)` from Step 1/Step 5's `state` output, since
  `WAITING` positions still occupy a slot). This cap may be higher than
  the usual 5 if last week cleared its profit goal with room to spare.

## Step 7 — Gate every proposed BUY/SELL

For every proposed trade, before doing anything else, run its risk-check
and then execute it (Step 8) before moving to the next proposed trade.
Don't batch all of a cycle's risk-checks ahead of execution — the
slot-cap check reads live ledger state, so each buy's risk-check must
run immediately before that specific buy executes, reflecting any buys
already executed earlier in this same cycle.

```
python -m robinhood_bot.cli risk-check buy SYMBOL --value <proposed dollar amount> --sector <symbol's sector from Step 2/Step 3 candidate data> --rsi <symbol's rsi from Step 2/Step 3 candidate data> --ma-bullish/--no-ma-bullish (omit if ma_trend_bullish is null) --golden-cross-bullish/--no-golden-cross-bullish (omit if golden_cross_bullish is null) --prices-json "<fresh quotes>"
python -m robinhood_bot.cli risk-check sell SYMBOL --prices-json "<fresh quotes>"
```

- If `"approved": false`, **do not execute this trade.** Read `"reason"`
  and either propose a smaller size / different symbol, or fall back to
  HOLD. Never override a rejection.
- A BUY is rejected if you already hold an active position in the same
  `--sector` (default limit: 1 position per sector) — the rejection
  `"reason"` names the sector; treat it exactly like any other
  rejection, never override it.
- A BUY is also rejected if the candidate's RSI is overbought (default
  threshold: 70), if `ma_trend_bullish` is explicitly `false` (no
  confirmed short-term uptrend), or if `golden_cross_bullish` is
  explicitly `false` (death cross — the 50-day average at or below the
  200-day average) — always pass `--rsi` from the candidate's data, and
  pass `--ma-bullish`/`--no-ma-bullish` and
  `--golden-cross-bullish`/`--no-golden-cross-bullish` only when the
  corresponding field is `true`/`false`; omit each flag entirely when
  it's `null` (not enough history to judge — the check is skipped
  rather than blocking on missing data).
- For an approved BUY, `"max_position_value"` is the ceiling. Compute a
  whole-share quantity: `floor(min(proposed_value, max_position_value) /
  fresh_quote_price)`. You may propose fewer shares than the ceiling
  allows.

## Step 8 — Execute approved trades

**If `trading_mode` is `"paper"`:**

```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --sector <same sector passed to Step 7's risk-check> --rsi <same rsi passed to Step 7's risk-check> --ma-bullish/--no-ma-bullish (matching Step 7's risk-check, omit if null) --golden-cross-bullish/--no-golden-cross-bullish (matching Step 7's risk-check, omit if null) --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote price> --reason "<why>"
```

Never call any live order-placement MCP tool in this mode.

**If `trading_mode` is `"live"`:**

**0. Resolve the account (once per cycle, reuse for every trade below).**
Call `get_accounts` and filter for `agentic_allowed: true`. Per this
project's setup (`USAGE.md`), exactly one such account should exist —
the dedicated Agentic account this bot is allowed to touch. If you find
zero or more than one, **stop and report this to the user instead of
guessing** — do not fall back to a non-agentic account or pick
arbitrarily among several. Cache the resulting `account_number` for
every MCP call below, for the rest of this cycle.

For each approved trade, in order (same one-at-a-time rule as Step 7 —
resolve and execute this trade fully before moving to the next):

**1. Check tradability and pick the session.** Call
`get_equity_tradability` for `account_number` + this symbol. This cycle
runs after market close, so regular hours is never the target session —
read which of `extended_hours` / `all_day_hours` the symbol is
*currently* eligible for and use that as `market_hours` below:
   - Eligible for `extended_hours` right now → use `extended_hours`.
   - Not eligible for `extended_hours` but eligible for `all_day_hours`
     (Robinhood's 24-hour market, later in the evening or for
     24-hour-eligible symbols) → use `all_day_hours`.
   - Eligible for neither right now → **skip this trade for this
     cycle** and note it in the summary as "not currently tradable in
     any live session" — do not fall back to a `regular_hours` order,
     since that would silently queue until tomorrow's open instead of
     executing against the data this cycle just gathered.

**2. Get the marketable limit price.** Call `get_equity_price_book` for
this symbol and read the top of book: use the best **ask** for a BUY,
the best **bid** for a SELL. This is the `limit_price` below — never the
Step 4/Step 1 quote, which can be seconds to minutes stale by now.

**3. Place the order.** Call `place_equity_order` directly — do not
call `review_equity_order` first; entering `"live"` mode is itself this
cycle's standing authorization, and `cli.py risk-check` (Step 7) is
already the hard, non-overridable gate on every trade.
   - `account_number`: from step 0.
   - `symbol`, `side` (`"buy"`/`"sell"`).
   - `type`: `"limit"`.
   - `limit_price`: from step 2, as a string.
   - `quantity`: the approved share count from Step 6/7, as a string.
   - `market_hours`: from step 1.
   - `ref_id`: a freshly generated UUID for this logical order (reuse
     the same one only if retrying this exact order after a transport
     failure — never on a new trade).

If `place_equity_order` itself fails or is rejected: do not call
`record-fill`. Leave the ledger untouched and note the failure and its
reason in your summary — surface it, don't retry silently or guess at
what happened.

**4. Confirm the fill before recording it.** A limit order — even a
marketable one — isn't guaranteed to fill synchronously in
`place_equity_order`'s own response. Call `get_equity_orders` with
`account_number` and the returned `order_id` to check `state`:
   - `filled`: use the response's actual filled quantity and average
     price for `record-fill` below.
   - `partially_filled`: check again once or twice more (a few seconds
     apart is enough for a marketable order in an open session); if it's
     still only partially filled after that, `record-fill` **only the
     quantity actually filled** at its average price, and note the
     unfilled remainder in your summary — never round up to the
     originally requested quantity.
   - `cancelled` / `rejected` / `failed` / `voided`: do not call
     `record-fill`. Report the terminal state and any reason plainly.
   - still `new` / `queued` / `confirmed` / `unconfirmed` after a few
     checks: treat as **unresolved** for this cycle — do not call
     `record-fill` (there's no confirmed fill data yet), and say so
     explicitly in the summary so the next stop-loss-sweep or daily
     cycle knows to check this order's status via `get_equity_orders`
     before assuming the position is (or isn't) open.

```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual average fill price> --sector <same sector passed to Step 7's risk-check> --rsi <same rsi passed to Step 7's risk-check> --ma-bullish/--no-ma-bullish (matching Step 7's risk-check, omit if null) --golden-cross-bullish/--no-golden-cross-bullish (matching Step 7's risk-check, omit if null) --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <actual filled qty> --price <actual average fill price> --reason "<why>"
```

## Step 9 — Summarize

Run `python -m robinhood_bot.cli state --prices-json "<fresh quotes>"
--closes-json "<closes object from Step 4>"` one more time and report to
the user:
- What trades were made today and why (or why none were made).
- Current `monthly_return_pct` against the monthly goal.
- Current `banked_cash`, if any of today's sells crossed the weekly
  profit-banking threshold — this is a one-way ratchet, so a growing
  balance across cycles is worth surfacing as a running "protected
  gains" figure, distinct from tradeable `cash`.
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
- **Mechanical profit/stop-loss sweep (not numbered in the live cycle
  above, since it's a separate skill there):** `python -m robinhood_bot.cli
  backtest check-stop-losses --run RUN_ID --asof <simulated date>
  --prices-json "<quotes from Step 4, covering active_positions AND
  long_hold_positions>" --apply`. This reports (and applies any
  `PROMOTE_LONG_HOLD` for) stop-loss breaches, and now also reports any
  `SELL` entries from the weekly profit-goal mechanism — still
  report-only for `SELL`, so execute them via Steps 7-8 below exactly
  like every other proposed trade.
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --value <proposed dollar amount, for buys> --sector <symbol's sector,
  for buys> --rsi <symbol's rsi, for buys> --ma-bullish/--no-ma-bullish
  (for buys, matching ma_trend_bullish, omit if null)
  --golden-cross-bullish/--no-golden-cross-bullish (for buys, matching
  golden_cross_bullish, omit if null) --prices-json "<quotes>"`, then on
  approval, `python -m robinhood_bot.cli backtest record-fill {buy|sell}
  SYMBOL --run RUN_ID --asof <simulated date> --qty <n> --price <quote
  price> --sector <same sector, for buys> --rsi <same rsi, for buys>
  --ma-bullish/--no-ma-bullish (matching, for buys)
  --golden-cross-bullish/--no-golden-cross-bullish (matching, for buys)
  --reason "<why>"`. There is no live-order-placement call in this
  mode, ever.
- **After all of today's decisions are executed:** `python -m
  robinhood_bot.cli backtest mark-day --run RUN_ID --asof <simulated
  date> --prices-json "<quotes from Step 4>"`. This records today's
  mark-to-market equity (cash + all held positions, valued at today's
  quotes) so `backtest report` has a full day-by-day equity curve to
  compute `max_drawdown_pct` from at the end — `backtest run`'s
  deterministic loop writes this same row internally every day, but this
  manual mode has no equivalent automatic step, so it must be called
  explicitly, once per simulated day.

### Summarize

After the last simulated day, run:

```
python -m robinhood_bot.cli backtest report --run RUN_ID
```

Report `total_return_pct`, `max_drawdown_pct`, `wins`/`losses`, and
`benchmark_return_pct` (buy-and-hold SPY over the same window) to the
user, alongside any symbols that were skipped for missing quotes.
