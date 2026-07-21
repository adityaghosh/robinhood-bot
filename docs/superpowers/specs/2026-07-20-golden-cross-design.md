# 50/200-Day SMA Golden Cross / Death Cross Signal — Design

Status: Approved for planning
Date: 2026-07-20

## Purpose

The existing RSI/MA-crossover feature added a *short-term* trend check
(5-day vs 20-day SMA) that gates entries and informs long-hold exits. This
adds a second, *longer-horizon* trend signal — the classic 50-day vs
200-day SMA "golden cross" (50 above 200, bullish regime) / "death cross"
(50 at or below 200, bearish regime) — computed the same way, gating
entries and informing long-hold exits the same way, just on a much wider
window. Manual backtests this session showed multi-week stretches where
every held position turned bearish together in a broad selloff; a
longer-horizon regime signal gives both the entry gate and the exit
judgment a second, slower-moving check that a 5/20 window can't see.

## Background: what already exists and doesn't need to change

- `is_bullish_ma_trend(closes: list[float], short_window: int = 5, long_window: int = 20) -> bool | None`
  in `universe.py` already takes arbitrary windows — it just happens to be
  called with (5, 20) today for the existing `ma_trend_bullish` field.
  This feature reuses it unchanged with (50, 200); no new indicator math.
- `relative_strength_index`, `combined_rank`, `realized_volatility`,
  `average_true_range_pct` are unchanged.
- The existing `ma_trend_bullish` mechanical gate and long-hold discretionary
  exit signal in `evaluate_buy`/SKILL.md are unchanged and continue to run
  independently — this is an additive second signal, not a replacement.

## Non-goals

- No new indicator function — `is_bullish_ma_trend` is reused with
  different window arguments.
- No discrete crossover-event detection (the exact day the 50-day crossed
  the 200-day) — same simplification the existing 5/20 check already
  makes: just the current relative position of the two SMAs.
- No CLI-tunable windows — `golden_cross_short_window_days`/
  `golden_cross_long_window_days` are code-level config defaults, matching
  every other threshold in this project.
- No change to `combined_rank` or universe filtering/ordering — this only
  affects whether a specific BUY is approved and what's surfaced for
  discretionary exit judgment, exactly like the existing MA-trend check.

## Architecture

### Config (`universe.py`)

```
UniverseConfig
  + golden_cross_short_window_days: int = 50
  + golden_cross_long_window_days: int = 200
```

### Data flow

- `Candidate` (universe.py) gains `golden_cross_bullish: bool | None`,
  computed in `build_universe` via
  `is_bullish_ma_trend(closes, cfg.golden_cross_short_window_days, cfg.golden_cross_long_window_days)`
  from the same daily bars already fetched for the other indicators —
  exactly how `ma_trend_bullish` is computed today, just a second call
  with different windows.
- The lookback buffer `build_universe` requests widens to cover
  `golden_cross_long_window_days + 1` (200+1 days) — the largest of all
  the indicator windows — for every candidate, matching how the existing
  buffer already covers `ma_long_window_days`. This applies to every
  universe candidate, not just held positions, per the same rationale
  RSI/MA-trend already established: the entry gate needs it on
  not-yet-held candidates too.
- `Position` (portfolio_state.py) gains `golden_cross_bullish: bool | None = None`,
  captured once at buy time and persisted via ledger.py's existing
  `.get(key, default)` pattern — mirrors `ma_trend_bullish` exactly.
- Like the 5/20 trend, this changes daily, so `backtest_commands.py`'s
  entries loop computes it fresh per candidate per day from
  `store.get_closes_window(...)` with the widened window, immediately
  before each `evaluate_buy` call — same pattern as `ma_trend_bullish`,
  not precomputed once per run.

### Entry gate (`risk_engine.py`)

`evaluate_buy` gains one new required parameter,
`golden_cross_bullish: bool | None`, with a new check placed after the
existing `ma_trend_bullish` check:

```python
if golden_cross_bullish is False:
    return BuyDecision(False, "long-term trend bearish (50-day SMA at or below 200-day SMA / death cross)", max_value)
```

`golden_cross_bullish is False` (not falsy) is deliberate, matching the
existing `ma_trend_bullish is False` check: `None` (insufficient 200-day
history) bypasses — a data gap must never fail closed and block an
otherwise-good trade.

### Wiring (`commands.py`, `backtest_commands.py`, `cli.py`)

- `cmd_risk_check`/`cmd_record_fill` and their backtest wrappers gain an
  optional `golden_cross_bullish: bool | None = None` parameter (defaulted,
  like `ma_trend_bullish`), so existing sell-path callers and tests are
  unaffected.
- `cmd_record_fill`'s buy branch persists it onto the newly created
  `Position`.
- `cli.py` gains a `--golden-cross-bullish` tri-state flag (via
  `argparse.BooleanOptionalAction`, same as `--ma-bullish`) on
  `risk-check`/`record-fill`, live and backtest variants.
- Live `universe` dispatch and the backtest `run` dispatch's candidate
  data both include `golden_cross_bullish` per candidate, same as
  `ma_trend_bullish` today.

### Exit side (`commands.py`, SKILL.md)

- `cmd_state`'s per-position summary (`_position_summary`) gains a
  `"golden_cross_bullish"` field, computed fresh from current price
  history at `cmd_state` call time — same rationale as `ma_trend_bullish`:
  the LLM needs today's reading, not the buy-time snapshot.
- `robinhood-trading/SKILL.md`'s LONG_HOLD guidance gains a note: a
  golden-cross flip (50-day SMA moving back above the 200-day) is a
  **stronger, higher-conviction** version of the existing short-term
  bounce signal. When the 5/20 trend *and* the 50/200 trend are both
  bullish at once, that's the clearest "sell into the bounce" case —
  when only the short-term trend has flipped, it's a weaker, more
  provisional read, since manual backtests this session showed 5/20
  flips reverse within days during choppy markets while a 50/200 flip
  is a slower, more durable signal.

## Testing Strategy

- `universe.py`: `build_universe` test confirming `golden_cross_bullish`
  appears correctly on a resolved `Candidate`, computed via the widened
  (50, 200) window — no new unit tests for `is_bullish_ma_trend` itself
  since it's reused unchanged and already covered.
- `risk_engine.py`: `evaluate_buy` tests for the new rejection case
  (death cross), plus a case proving `golden_cross_bullish=None` bypasses
  the check (approved), plus updating every existing `evaluate_buy` test
  call with the new required argument (explicit ripple to flag in the
  plan, same as the RSI/MA rollout).
- `commands.py`/`backtest_commands.py`: tests for the new optional
  `golden_cross_bullish` parameter on `cmd_risk_check`/`cmd_record_fill`
  (rejection reason, persistence onto the new `Position`), and a
  `cmd_state` test confirming a held position's summary includes a fresh
  `golden_cross_bullish` field.
- `backtest_commands.py`: a hand-verified `cmd_backtest_run` integration
  test where a death-cross candidate is rejected in the entries loop in
  favor of the next-ranked candidate — same shape as the RSI/MA
  integration test.
- `cli.py`: `--golden-cross-bullish` flag tests on `risk-check`/`record-fill`
  (live and backtest); `universe` output includes the new field per
  candidate.

## Error Handling

- Insufficient price history (<200 days) for the golden-cross reading:
  `is_bullish_ma_trend` returns `None`, which the entry gate treats as
  "can't confirm, don't block" — identical to the existing 5/20 check's
  insufficient-history handling.
- No new I/O or failure modes: this is a second call to an already-tested
  pure function with different arguments.

## Open Items for Follow-up (not blocking this spec)

- Whether 200 trading days of history is reliably available for every
  candidate in this bot's actual data source, or whether some symbols
  (recent IPOs, e.g. SPCX observed this session) will spend most/all of
  a backtest window with `golden_cross_bullish=None` — left to observe
  in practice, since `None` always bypasses safely either way.
- Whether the "both 5/20 and 50/200 bullish" combined framing in
  SKILL.md needs a more formal joint signal later — left as prose
  guidance for the LLM's discretionary judgment for now, not a new
  mechanical check.
