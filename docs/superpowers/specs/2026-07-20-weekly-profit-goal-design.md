# Weekly Dollar Profit Goal — Design

Status: Approved for planning
Date: 2026-07-20

## Purpose

Replace the per-position percentage profit target (`profit_target_pct`,
currently 8%) with a portfolio-level, dollar-denominated weekly profit
goal (default $500). The per-position percentage felt arbitrary and hard
to reason about; a fixed weekly dollar figure is easier to hold in your
head, and — critically — gives a concrete, at-a-glance answer to "have I
already banked enough this week that I can afford to cut a lagging
position?" when making stop-loss-sweep decisions.

This changes the shared `risk_engine.py`, so it affects live trading,
paper trading, and both backtest modes identically — consistent with
this project's existing principle that backtesting reuses the exact same
risk logic as live trading, never a separate approximation of it.

## Background: what stays exactly as it is

The stop-loss side of the risk engine is untouched: `stop_loss_pct`
(5%), `grace_period_days` (5), and the `WAITING`/`LONG_HOLD` state
machine driven by `evaluate_position` all keep their current behavior
and thresholds. Buy-side sizing (`max_new_position_value`,
`evaluate_buy`) and the monthly circuit breaker
(`monthly_circuit_breaker_pct`) are also unchanged. Only the
profit-taking mechanism changes.

## Non-goals

- Converting the stop-loss threshold to a dollar figure — stays a
  percentage.
- Changing how a position *enters* `LONG_HOLD` (still purely the
  existing stop-loss/grace-period path) — only exit *eligibility* once
  there changes.
- Retroactively reinterpreting backtests already run under the old 8%
  rule (`year-2025-2026`, `q3-2026`, `llm-2wk-2026-07`) — those stand as
  historical record of the old mechanism. A comparable run under the new
  rule is a fresh backtest after this ships.
- A configurable choice of "biggest winner first" vs. some other
  ordering for the batch sell — biggest-first is the only ordering this
  design implements.

## Architecture

### Data model changes

`RiskConfig` (`robinhood_bot/risk_engine.py`):
- Remove `profit_target_pct`.
- Add `weekly_profit_goal: float = 500.0` — a *step size*, not a
  one-time ceiling (see "Tier escalation" below).

`PortfolioState` (`robinhood_bot/portfolio_state.py`):
- Add `week: str = ""` — an ISO calendar week key, e.g. `"2026-W29"`,
  mirroring how `month` already stores `"YYYY-MM"`.
- Add `week_realized_pnl: float = 0.0` — cumulative realized dollar P&L
  (gains minus losses) from every sell since the current week started.
- New `roll_week_if_needed(state: PortfolioState, today: date) -> PortfolioState`,
  structurally identical to the existing `roll_month_if_needed`: resets
  `week_realized_pnl` to `0.0` whenever `today`'s ISO week differs from
  `state.week`.

`ledger.py`'s `state_from_dict` reads both new fields via
`.get(key, default)`, exactly like `month`/`month_start_equity` already
are — an old saved `ledger.json` without these keys loads fine and rolls
to the correct current week on first use. No migration step needed.

`commands.cmd_record_fill`'s SELL branch gains one line:
`state.week_realized_pnl += (price - position.qty's entry_price) * qty`
— every executed sell updates the weekly tally, regardless of whether it
was triggered by the new profit-goal mechanism, a stop-loss/grace-period
promotion-turned-later-sale, or a fully discretionary Claude exit (as
demonstrated live in this session's `llm-2wk-2026-07` backtest, where
both realized sells were discretionary, not mechanical).

### The core mechanic: `evaluate_profit_exits`

A new function in `robinhood_bot/risk_engine.py`, replacing the SELL
branch that `evaluate_position` used to have:

```python
def evaluate_profit_exits(
    positions: list[Position], prices: dict[str, float], week_realized_pnl: float, cfg: RiskConfig,
) -> list[Position]:
    gains = []
    for position in positions:
        price = prices.get(position.symbol)
        if price is None:
            continue
        gain = (price - position.entry_price) * position.qty
        if gain > 0:
            gains.append((gain, position))
    gains.sort(key=lambda g: g[0], reverse=True)

    tier = (int(week_realized_pnl // cfg.weekly_profit_goal) + 1) * cfg.weekly_profit_goal
    to_sell = []
    running = week_realized_pnl
    for gain, position in gains:
        if running >= tier:
            break
        to_sell.append(position)
        running += gain
    return to_sell
```

`positions` is always `state.active_positions + state.long_hold_positions`
combined at the call site — a recovered long-hold position is exactly as
eligible as an active winner. A missing quote skips that candidate for
this evaluation (never fabricated), consistent with every other
price-lookup rule in this codebase.

**Tier escalation.** `weekly_profit_goal` is a step, not a ceiling: the
function computes `tier` fresh each call from however many full
multiples of `weekly_profit_goal` are already banked this week
(`week_realized_pnl`), then greedily takes the biggest winners until
*that* tier is cleared. If $500 was already banked earlier in the week,
today's tier is $1000; once that clears, the next call (tomorrow, or
later the same day after a reload) chases $1500. This means hitting the
goal early in the week raises the bar rather than shutting off
profit-taking for the rest of the week, while still stopping for the
day once the current tier is cleared — winners that don't need to be
sold yet keep running, day to day, exactly like before.

**Negative `week_realized_pnl`.** If net realized P&L this week is
currently negative (e.g. after a discretionary loss-cutting sell, as
happened twice in this session's `llm-2wk-2026-07` backtest), the tier
formula computes `tier = 0` rather than a negative number — the
function chases breakeven first, then continues on to the next full
`weekly_profit_goal` tier above zero once that's cleared. This falls
out of the formula naturally rather than being special-cased, but is
worth stating explicitly since it wasn't discussed as its own decision
point.

`evaluate_position` loses its `profit_target_pct` branch entirely and
now only ever returns `HOLD` or `PROMOTE_LONG_HOLD` — it no longer
decides SELL under any circumstance. All profit-side SELL decisions
come exclusively from `evaluate_profit_exits`.

### Call-site changes

**`commands.cmd_check_stop_losses`** (live/paper path, report-only
contract unchanged): the per-position `evaluate_position` loop is
simpler now (no more "SELL reported but not executed" special case,
since `evaluate_position` can't return SELL). After that loop, it calls
`evaluate_profit_exits(state.active_positions + state.long_hold_positions,
prices, state.week_realized_pnl, cfg)` and appends a `SELL` result entry
per position returned — still **report-only**; this function has never
executed a sell itself, and that doesn't change. The caller (the
stop-loss-sweep skill, or a human) executes it via `record-fill`
separately, exactly as today.

**`backtest_commands.cmd_backtest_run`** (deterministic path,
self-executing): the daily loop's shape gains one phase:
1. Per-position `evaluate_position` loop (unchanged, minus the old SELL
   branch) — only ever produces `HOLD`/`PROMOTE_LONG_HOLD`.
2. Reload state; call `roll_month_if_needed` (existing) **and**
   `roll_week_if_needed` (new); save. Week must roll before step 3 reads
   `week_realized_pnl`, so a new week's tier starts fresh.
3. Call `evaluate_profit_exits` using each candidate's current price via
   `store.get_close(symbol, today)` and the now-current
   `state.week_realized_pnl`. For each position returned, execute the
   sell directly via `commands.cmd_record_fill(..., "sell", ..., today,
   "weekly profit-goal exit")` — the same direct-execution pattern the
   old profit-target branch used.
4. Reload state; proceed to entries (free-slot count now correctly
   reflects any profit-taking sells from step 3) — unchanged from here.
5. Equity curve append — unchanged, just reflects the new final state.

**`commands.cmd_state`** (used by both live/paper `state` and
`cmd_backtest_state`): calls `roll_week_if_needed` alongside its
existing `roll_month_if_needed` call. The returned dict gains two
fields: `week_realized_pnl` (banked so far this week) and
`week_profit_target` (the current tier ceiling, computed the same way
`evaluate_profit_exits` computes `tier`) — the field that directly
answers "how much room do I have before/after this week's goal," the
context this whole change is meant to surface for loss-sweep decisions.

## SKILL.md updates

**`.claude/skills/robinhood-stop-loss-sweep/SKILL.md`:**
- Step 1: note symbols from `long_hold_positions` too, not just
  `active_positions` — both are now eligible profit-exit candidates.
- Step 2: fetch fresh quotes for those as well.
- Step 3: `--prices-json` must cover both position sets (same flag,
  wider symbol set supplied by the caller).
- Step 4: update the hardcoded reason text (`"stop-loss sweep: profit
  target hit"` → `"weekly profit-goal exit"`), and note the `<held qty>`
  lookup now needs to check both `active_positions` and
  `long_hold_positions` from Step 1, since a sold symbol could be
  either.

**`.claude/skills/robinhood-trading/SKILL.md`:**
- Step 6: profit-taking is no longer a per-position judgment call —
  it's fully mechanical now (surfaced through `check-stop-losses`).
  Step 6's remaining discretion is early stop-loss-side exits (as
  demonstrated with MRVL/IBM in `llm-2wk-2026-07`) and long-hold
  judgment that goes further than the mechanical floor now under it.
- Steps 1/5: note `week_realized_pnl`/`week_profit_target` as fields to
  read from `state`, meant to directly inform loss-sweep judgment.
- Backtest Mode section: formalize the `backtest check-stop-losses
  --apply` step that was improvised live during the `llm-2wk-2026-07`
  run but never actually documented — this is the natural point to fix
  that gap alongside the mechanism it now also serves.

## Testing Strategy

- **`test_risk_engine.py`:** remove the `profit_target_pct`-based SELL
  tests from `evaluate_position`'s suite. Add tests for
  `evaluate_profit_exits`: single winner closing the gap alone, multiple
  winners with biggest-first ordering, tier escalation when part of this
  week's goal is already banked, long-hold positions included as
  candidates, a missing quote skipping that candidate, and no
  positive-gain positions returning `[]`.
- **`test_portfolio_state.py`:** `roll_week_if_needed` tests mirroring
  the existing `roll_month_if_needed` tests (rolls on ISO-week change,
  no-op within the same week).
- **`test_commands.py`:** update `cmd_check_stop_losses` tests (SELL
  results now originate from the batch call, still report-only); update
  `cmd_state` tests for the two new fields; update `cmd_record_fill`
  tests to confirm `week_realized_pnl` accumulates on every sell
  (including a loss, since it's net P&L, not gains-only).
- **`test_backtest_commands.py`:** the existing `cmd_backtest_run` hand-
  verified test relies entirely on the old 8% mechanism for its day-2
  SELL trigger and needs a genuine rewrite around the new mechanic —
  this is also the right place to add end-to-end coverage of tier
  escalation and long-hold eligibility inside the deterministic loop,
  not just at the `evaluate_profit_exits` unit level.
- **`test_cli.py`:** update any output-shape assertions on `state` that
  didn't already account for the two new fields.

## Error Handling

- A missing quote for a profit-exit candidate: that candidate is simply
  excluded from consideration for this evaluation, never fabricated or
  assumed — consistent with every other price-lookup rule in this
  codebase.
- `week_realized_pnl` only ever changes via `cmd_record_fill`'s sell
  branch — never mutated directly by `evaluate_profit_exits` itself,
  which is a pure function with no side effects, matching the existing
  `evaluate_position`/`evaluate_buy` pattern.

## Open Items for Follow-up (not blocking this spec)

- Whether `weekly_profit_goal`'s default of $500 is the right starting
  point in practice — left as a `RiskConfig` default to tune after
  observing real behavior, same as every other threshold in this file.
- Whether the tier-escalation step size should ever differ from the
  base `weekly_profit_goal` (e.g. smaller increments after the first
  tier) — not requested, not implemented here.
