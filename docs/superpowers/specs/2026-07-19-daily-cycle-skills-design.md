# Daily Trading Cycle & Stop-Loss Sweep Skills — Design

Status: Approved for planning
Date: 2026-07-19

## Purpose

Wire together the core engine (`docs/superpowers/specs/2026-07-18-robinhood-agentic-monthly-trading-design.md`)
and the universe module (`docs/superpowers/specs/2026-07-19-universe-fetch-design.md`)
into the two Claude Code skills that actually run the bot: a daily
research-and-trade cycle, and a mechanical intraday stop-loss safety net.
This is "Plan 2b" — the last piece before the bot is manually runnable
end to end, once Robinhood's Agentic Trading MCP is connected.

## Non-goals

- Connecting to Robinhood's MCP server itself (the one-time `claude mcp
  add` / OAuth step) — a prerequisite the user completes separately, not
  something this spec builds.
- Scheduled/automated invocation (Claude Code routines, cron) — for now
  both skills are invoked manually, one trading day at a time, while the
  strategy and MCP connection are still being validated by hand. Routine
  wiring is future work once the manual loop is trusted.
- Exact numeric parameter tuning (already deferred in the core-engine
  spec) and the performance-tracking/"learning" feature (separately
  deferred, to be brainstormed once there's real trade history).

## New Python Surface: `TRADING_MODE`

The only code change in this spec. `cli.py` gains a module-level
constant, `TRADING_MODE = "paper"` (or `"live"`), following the existing
pattern of `LEDGER_PATH`/`TRADE_LOG_PATH`/`UNIVERSE_CACHE_PATH`/
`STARTING_CASH`. `cmd_state`'s JSON output gains a `"trading_mode"`
field surfacing this value. Both skills' first step is always "call
`cli.py state` and read `trading_mode` from the result" — mode can never
drift out of sync between what the code does and what the skill's
instructions believe, and switching modes stays a one-line code edit.

No other `cli.py` command needs to branch on `TRADING_MODE`: the
paper/live distinction is entirely about whether the *skill* calls the
real Robinhood MCP `place_equity_order` tool before recording a fill —
`cmd_record_fill` itself just records whatever fill actually happened,
identically in both modes.

## Candidate Shortlisting (skill instruction, not new code)

`cli.py universe` returns roughly 123 ranked candidates — too many to
research in depth every day. The daily-cycle skill's instructions
specify a shortlist rule Claude applies itself by reading the returned
JSON, rather than a new deterministic `cli.py` flag:

- Top 15 candidates by `combined_rank` from the `sp500`/`nasdaq100`
  categories.
- All `leveraged`-category candidates (TQQQ, UPRO, SOXL — only 3, so
  this simply means "always include them").
- Any symbol currently held (active or long-hold) not already covered
  above, so open positions are never silently dropped from
  consideration.

This is deliberately left as an instruction rather than enforced in
Python: it's a cost/quality tradeoff on research breadth, not a safety
constraint. Unlike a missed `risk-check` gate (which could mean an
unauthorized trade), researching a slightly different N on some day has
no correctness consequence.

## Daily Trading Cycle Skill

Directory: `.claude/skills/robinhood-trading/SKILL.md`. Invoked manually
(`/robinhood-trading` or however the harness surfaces it) once per
trading day, after market close per the core-engine spec's cadence
decision.

Procedure:

1. **Read mode & holdings.** `cli.py state --prices-json '{}'` — prices
   come back marked stale, which is fine here; this call is only to
   learn `trading_mode` and the current active/long-hold symbols.
2. **Get ranked universe.** `cli.py universe` (uses the weekly cache by
   default; `--refresh` only if explicitly requested).
3. **Build the shortlist** per the rule above.
4. **Get fresh quotes.** Robinhood MCP's quote tool, for every
   shortlisted and held symbol. Any symbol whose quote fails is skipped
   for this cycle — never fabricate a price, per the core-engine spec's
   error-handling rule.
5. **Refresh state with real prices.** `cli.py state --prices-json
   '<fresh quotes>'` — equity, unrealized P&L, and monthly-return
   progress are now accurate.
6. **Research and decide, per shortlisted symbol.**
   - Held positions: consider lifecycle status (ACTIVE/WAITING/
     LONG_HOLD) and unrealized P&L; propose SELL or HOLD.
   - Unheld candidates: consider volatility rank and recent price
     action; propose BUY or skip, respecting the 5-slot cap.
7. **Gate every proposed BUY/SELL.** `cli.py risk-check {buy|sell}
   SYMBOL --value <$> --prices-json <fresh quotes>`. A rejection means
   the trade is not placed; Claude may propose an alternative symbol/
   size or fall back to HOLD. Position sizing: `risk-check`'s returned
   `max_position_value` divided by the fresh quote price, floored to a
   whole share count, is the ceiling — the skill may propose smaller.
8. **Execute approved trades.**
   - Paper mode: `cli.py record-fill {buy|sell} SYMBOL --qty <n> --price
     <fresh quote> --reason "<why>"`.
   - Live mode: call the Robinhood MCP `place_equity_order` tool, then
     `cli.py record-fill` using the *actual* returned fill quantity/
     price from the order response — never the pre-trade quote.
9. **Summarize.** Final `cli.py state`, monthly-goal progress, and a
   plain-English recap of what was done and why, for the user to review.

## Stop-Loss Sweep Skill

Directory: `.claude/skills/robinhood-stop-loss-sweep/SKILL.md`. Invoked
manually at a second point in the trading day (e.g. midday), per the
core-engine spec's intraday safety-net design. Deliberately mechanical —
no research, no discretion — so it's safe to run without the full daily
cycle's judgment calls.

Procedure:

1. `cli.py state --prices-json '{}'` → active/waiting position symbols.
2. Robinhood MCP quote tool → fresh price per symbol. Any symbol whose
   quote fails is skipped this sweep — never fabricated.
3. `cli.py check-stop-losses --prices-json <fresh> --apply`.
   - `PROMOTE_LONG_HOLD` results: already applied by `--apply` (the
     ledger already moved the position); nothing further to do.
   - `SELL` results: execute now — paper mode via `record-fill`; live
     mode via `place_equity_order` then `record-fill`, same pattern as
     the daily cycle's execution step.
   - `SKIP`/`HOLD` results: nothing to do.
4. One-line report: what (if anything) sold, and current cash/position
   count. No further reasoning.

## MCP Tool Names

Referenced by both skills, sourced from research into Robinhood's
Agentic Trading MCP (launched 2026-05-27): `get_accounts`,
`get_portfolio`, `get_equity_positions`, `get_equity_quotes`,
`get_equity_orders`, `place_equity_order`, `cancel_equity_order`. Since
the MCP connection is not yet established in this environment, these
names are documented as best-known-as-of-writing rather than verified
against a live tool list. Both skills should be written to describe the
*capability* needed ("the Robinhood MCP quote tool") alongside the
expected tool name, so a name drift when the connection is finally made
is a one-line fix in the skill text, not a redesign.

## Testing Strategy

SKILL.md files are prompt/procedure text, not code — there is no
automated test for them. Verification is the manual end-to-end run
already specified in the core-engine spec's testing strategy: once
Robinhood's MCP is connected, run the daily-cycle skill by hand in paper
mode against this repo, with the real MCP connection active for
read-only quotes, and confirm the resulting ledger/ trade log look
correct. The `TRADING_MODE` code addition (`cmd_state`'s new
`"trading_mode"` field) is the one piece of this spec that does get a
`pytest` unit test.

## Open Items for Follow-up (not blocking this spec)

- Verifying the actual Robinhood MCP tool names/signatures once
  connected, and updating both skills if they differ from what's
  documented above.
- Scheduled/routine invocation, once the manual loop is trusted.
- Shortlist size (15) and the stop-loss-sweep's exact time-of-day are
  reasonable defaults, not exhaustively tuned.
