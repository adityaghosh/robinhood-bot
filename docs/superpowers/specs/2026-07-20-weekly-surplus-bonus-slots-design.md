# Weekly Surplus Bonus Slots — Design

Status: Approved for planning
Date: 2026-07-20

## Purpose

The weekly dollar profit-goal mechanism (shipped earlier this session) tracks
`week_realized_pnl` against a `weekly_profit_goal` (default $500) and
mechanically sells winners to hit and then escalate past that goal within
the same week. But once a strong week clears the goal, any additional
profit earned that week currently has no effect beyond triggering more
same-week profit-taking — it doesn't change anything about how the bot
trades the *following* week. This adds a mechanism so a week's surplus
profit (realized P&L beyond that week's goal) becomes extra trading
capacity — additional active-position slots — for the week right after it,
consistent with this project's "reward a hot streak with modestly more
capacity, not more risk-per-trade" philosophy already reflected in the
existing long-hold-capital-utilization sizing curve.

## Background: what already exists and doesn't need to change

- `RiskConfig.weekly_profit_goal` (default $500) and `current_weekly_tier`/
  `evaluate_profit_exits` (the within-week escalating profit-taking sweep)
  are unchanged — this is a separate mechanism layered on top, not a
  replacement.
- `PortfolioState.week`/`week_realized_pnl` and `roll_week_if_needed`
  (ISO-week tracking, resets `week_realized_pnl` to 0 on a new week) already
  exist and are extended, not replaced.
- Position sizing (`max_position_pct`, `min_position_pct`,
  `long_hold_capital_cap_pct`, `max_new_position_value`) is entirely
  unaffected — this feature only ever changes the *count* of allowed active
  slots, never how large any individual position can be.

## Non-goals

- No change to `current_weekly_tier`/`evaluate_profit_exits` — the
  within-week profit-taking escalation stays exactly as shipped.
- No multi-week stacking, accumulation, or decay of bonus slots — each
  week's bonus is computed fresh from only the immediately preceding
  week's surplus. An unused bonus slot does not carry forward, and a
  losing or break-even prior week simply grants 0 bonus slots (no
  clawback of anything already spent).
- No change to the monthly circuit breaker or long-hold capital cap.
- No CLI-tunable bonus rate — `max_bonus_active_slots` is a `RiskConfig`
  default, matching how every other risk threshold in this project is
  only adjustable by editing the code default.
- No separate "dollars per bonus slot" config value — the bonus rate
  reuses `weekly_profit_goal` itself as the unit, to keep the mental model
  to one number ("clear a whole extra goal's worth of surplus, get a whole
  extra slot").

## Architecture

### Data model (`portfolio_state.py`, `ledger.py`)

```
PortfolioState (dataclass)
  + prior_week_realized_pnl: float = 0.0   # defaulted, backward-compatible
```

`roll_week_if_needed` captures the outgoing week's final `week_realized_pnl`
into `prior_week_realized_pnl` immediately before resetting
`week_realized_pnl` to 0:

```python
def roll_week_if_needed(state: PortfolioState, today: date) -> PortfolioState:
    iso_year, iso_week, _ = today.isocalendar()
    current_week = f"{iso_year:04d}-W{iso_week:02d}"
    if state.week != current_week:
        state.prior_week_realized_pnl = state.week_realized_pnl
        state.week = current_week
        state.week_realized_pnl = 0.0
    return state
```

`ledger.py`'s `state_to_dict`/`state_from_dict` persist/read
`prior_week_realized_pnl` the same backward-compatible way as every other
field added this session (`.get("prior_week_realized_pnl", 0.0)` on read,
defaulting old ledger files to `0.0`, i.e. no bonus).

### Bonus computation (`risk_engine.py`)

```
RiskConfig
  + max_bonus_active_slots: int = 2

bonus_active_slots(prior_week_realized_pnl: float, cfg: RiskConfig) -> int
  surplus = prior_week_realized_pnl - cfg.weekly_profit_goal
  if surplus <= 0: return 0
  return min(cfg.max_bonus_active_slots, int(surplus // cfg.weekly_profit_goal))
```

Worked examples at the default `weekly_profit_goal=500,
max_bonus_active_slots=2`: prior week $1,200 (surplus $700) → 1 bonus slot;
prior week $1,700+ (surplus ≥ $1,200) → capped at 2 bonus slots; prior week
$500 or less (surplus ≤ 0) → 0 bonus slots, including any losing week.

### Wiring into slot-count checks (`risk_engine.py`, `backtest_commands.py`)

`evaluate_buy`'s existing hard slot-count check becomes an effective cap
that adds this week's bonus (computed from last week's surplus, already
sitting on `state`) — no new parameter needed on `evaluate_buy` itself,
since it already receives both `state` and `cfg`:

```python
effective_max_active_positions = cfg.max_active_positions + bonus_active_slots(state.prior_week_realized_pnl, cfg)
if state.active_slot_count() >= effective_max_active_positions:
    return BuyDecision(False, "no active slots available", max_value)
```

Because this lives inside `evaluate_buy` itself, `commands.py`,
`backtest_commands.py`'s wrapper functions, and `cli.py` all need **zero
signature changes** for this part — the bonus applies automatically
wherever `evaluate_buy` is already called.

The one duplicate of the slot-count arithmetic is `cmd_backtest_run`'s
entries loop, which pre-computes `free_slots` as an optimization to skip
`rank_candidates_as_of` entirely when there's obviously no room. This must
use the same effective cap, or a bonus week would be silently under-filled
in the backtest:

```python
free_slots = (cfg.max_active_positions + bonus_active_slots(state.prior_week_realized_pnl, cfg)) - state.active_slot_count()
```

### Visibility (`commands.py`, `SKILL.md`)

`cmd_state`'s output dict gains two new fields, computed once per call:

```python
"prior_week_realized_pnl": state.prior_week_realized_pnl,
"effective_max_active_positions": cfg.max_active_positions + bonus_active_slots(state.prior_week_realized_pnl, cfg),
```

`robinhood-trading/SKILL.md` is updated in two places: Step 1 (read
mode & holdings) notes `prior_week_realized_pnl` as context for why the
cap might be elevated this week; Step 6 (research and decide) replaces its
current hardcoded "5 - len(active_positions)" free-slot formula with
`effective_max_active_positions - len(active_positions)`.

## Testing Strategy

- `portfolio_state.py`: `roll_week_if_needed` tests for capturing the
  outgoing week's `week_realized_pnl` into `prior_week_realized_pnl` on a
  week rollover, and leaving `prior_week_realized_pnl` untouched when
  staying within the same week.
- `ledger.py`: round-trip persistence test for `prior_week_realized_pnl`,
  plus a backward-compatible default-to-`0.0` test for old ledger files
  missing the key.
- `risk_engine.py`: `bonus_active_slots` unit tests for the zero case
  (surplus ≤ 0, including a losing prior week), the one-slot case, the
  capped-at-max case, and an exact-multiple boundary case (e.g. surplus
  exactly equal to `weekly_profit_goal` grants exactly 1 slot, not 0).
  `evaluate_buy` tests: a buy that would exceed `max_active_positions` but
  is allowed by a bonus slot (approved); a buy that exceeds even the
  boosted effective cap (rejected).
- `commands.py`: `cmd_state` test asserting both new output fields are
  present and correctly computed from a seeded `prior_week_realized_pnl`.
- `backtest_commands.py`: an integration test proving `cmd_backtest_run`'s
  entries loop actually fills a bonus slot that `max_active_positions`
  alone would have blocked (a direct analog to the existing
  `test_cmd_backtest_run_executes_deterministic_entry_exit_cycle`-style
  hand-verified scenario, seeded with a `prior_week_realized_pnl` above
  goal).

## Error Handling

- Old ledger files without `prior_week_realized_pnl` default to `0.0` —
  equivalent to "no bonus," never a crash or a fabricated value.
- `bonus_active_slots` is a pure function with no I/O and no failure mode
  beyond normal arithmetic; negative or zero surplus always yields exactly
  `0`, never a negative slot count.

## Open Items for Follow-up (not blocking this spec)

- Whether `max_bonus_active_slots=2` is the right ceiling in practice —
  left as a `RiskConfig` default to tune later, same as every other
  threshold in this file.
- Whether a similar surplus-to-capacity mechanism should someday apply to
  position sizing rather than slot count — explicitly out of scope now
  (see Non-goals), noted here only as a possible future direction.
