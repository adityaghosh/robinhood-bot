# Wilder's Smoothing RSI Fix — 2026-07-21

## Background

`robinhood_bot/universe.py`'s `relative_strength_index` computed RSI as a
simple average of gains/losses over only the trailing `window_days` price
changes, discarding the rest of the `closes` list passed in. Every real
platform (Robinhood included) uses Wilder's smoothing method: seed an
initial average over the first `window_days` changes, then roll it forward
across the entire remaining history with
`avg = (avg * (window_days - 1) + new_value) / window_days`.

Verified against Robinhood's own RSI for AAPL as of 2026-07-20 close:
Robinhood reported 63.90, our simple-average calc gave 82.10 on the same
data, and Wilder's method by hand gave 63.88 — a near-exact match. This
likely explains RSI readings pinned near the 0/100 extremes in recent
backtests.

## The fix

`robinhood_bot/universe.py`, function `relative_strength_index`:

```diff
 def relative_strength_index(closes: list[float], window_days: int = 14) -> float:
     if len(closes) < window_days + 1:
         return 50.0
-    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - window_days, len(closes))]
-    gains = [c for c in changes if c > 0]
-    losses = [-c for c in changes if c < 0]
-    avg_gain = sum(gains) / window_days
-    avg_loss = sum(losses) / window_days
+    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
+    gains = [max(c, 0.0) for c in changes]
+    losses = [max(-c, 0.0) for c in changes]
+    avg_gain = sum(gains[:window_days]) / window_days
+    avg_loss = sum(losses[:window_days]) / window_days
+    for i in range(window_days, len(changes)):
+        avg_gain = (avg_gain * (window_days - 1) + gains[i]) / window_days
+        avg_loss = (avg_loss * (window_days - 1) + losses[i]) / window_days
     if avg_loss == 0:
         return 100.0 if avg_gain > 0 else 50.0
     rs = avg_gain / avg_loss
     return 100.0 - (100.0 / (1.0 + rs))
```

No caller changes were needed. Every call site (`universe.py`'s
`build_universe`, `backtest_commands.py`'s `cmd_backtest_state` and
`cmd_backtest_run`, `cli.py`'s live `state` dispatch) already fetches a
widened lookback (up to `golden_cross_long_window_days=200` days) and passes
the full closes list into `relative_strength_index`. The old code wastefully
discarded all but the trailing `window_days + 1` entries of that list; the
new code uses the full history via Wilder's recurrence, so every caller gets
genuine Wilder smoothing for free.

## New TDD test

Added `test_relative_strength_index_uses_wilder_smoothing_over_full_history`
in `tests/test_universe.py`, using a 40-close synthetic series (well beyond
`window_days + 1 = 15`, so Wilder's recurrence actually rolls forward past
the initial seed). Expected values were computed by running both the old
and new formulas in a scratch Python script against this exact closes list
(not derived by hand):

- Old (simple trailing-average) RSI: `61.53846153846153`
- New (Wilder-smoothed) RSI: `64.0932449136437`

The test asserts the new function matches the Wilder value and explicitly
does NOT match the old simple-average value, for regression documentation.

Confirmed the test FAILED against the unfixed implementation (got
`61.53846153846153`, expected `64.0932449136437`) before applying the fix,
and PASSED after.

## Existing test coverage — inspected, not assumed

Grepped all of `tests/test_universe.py`, `tests/test_backtest_commands.py`,
`tests/test_cli.py`, and `tests/test_commands.py` for RSI-related
assertions. None needed value corrections, for the following
inspected-not-assumed reasons:

- `test_relative_strength_index_insufficient_data_is_neutral` — 3 closes and
  `[]`, both under `window_days + 1 = 15`; short-circuits to `50.0` in both
  old and new code, untouched by the Wilder change.
- `test_relative_strength_index_all_gains_is_100`,
  `test_relative_strength_index_all_losses_is_zero`,
  `test_relative_strength_index_mixed_known_value` — all use exactly 15
  closes (`window_days + 1`). With only 15 closes there are only 14 changes,
  so Wilder's rolling-forward loop (`range(window_days, len(changes))`)
  never executes — the seed average IS the final average, identical to the
  old trailing-window calculation. Confirmed by inspection of `len(closes)`
  in each test, not assumption.
- `test_build_universe_includes_rsi_and_ma_trend_on_candidate` (25 bars,
  monotonic `100.0 + i`) and the golden-cross candidate test (201 bars,
  monotonic `100.0 + i * 0.1`) — all price changes are positive, so
  `avg_loss` stays exactly `0.0` through every step of Wilder's recurrence
  (0 seeded, and `losses[i]` is always `0.0` for a monotonic series), giving
  RSI `100.0` under both formulas.
- `test_cmd_backtest_state_includes_fresh_rsi_for_held_position` (25 bars,
  monotonic `100.0 + i`) — same reasoning, RSI pinned at `100.0` under both
  formulas.
- `test_cmd_backtest_run_rejects_overbought_candidate_for_next_ranked` —
  AAPL2's monotonic +1/day series again pins RSI at exactly `100.0` under
  both formulas (still rejected as overbought either way). JPM's
  alternating `+0.1/-0.1` series is exactly symmetric, so both old and new
  formulas converge on `avg_gain == avg_loss`, giving RSI `~50` either way;
  this test only asserts *which symbol gets bought*, not an exact RSI value,
  so it is unaffected.
- Values like `rsi=62.0`, `rsi=81.3`, `rsi=50.0`, `rsi=45.0` in
  `tests/test_cli.py` and `tests/test_commands.py` are hardcoded inputs
  passed directly into `Candidate`/`Position`/CLI args — they are not
  computed by `relative_strength_index` at all, so they are unrelated to
  this change.

Ran the full suite (`python -m pytest tests/ -v`) after the fix: **232
passed, 0 failed** — no test needed a value correction beyond the comment
update below.

## Step 8: death-cross test check (`test_cmd_backtest_run_rejects_death_cross_candidate_for_next_ranked`)

This test's stale comment claimed AAPL2's tail gave "14-day RSI at ~64.3"
and JPM "~62.5", both computed under the old simple-average formula, and
asserts JPM (not AAPL2) ends up bought.

To recompute the actual production RSI value under the new Wilder formula,
I did not hand-derive it — I wrote a scratch script that:

1. Reconstructs the exact same `aapl2_bars`/`jpm_bars` HistoricalBar lists
   from the test (250-day AAPL2 series: 200-day decline + 50-day choppy
   recovery; JPM's gentle +0.01/day drift with +/-0.02 wobble).
2. Feeds them through the real `HistoricalPriceStore.get_closes_window`
   (the same call `cmd_backtest_run`'s entries loop makes), with
   `indicator_lookback = max(rsi_window_days + 1, ma_long_window_days,
   golden_cross_long_window_days) = 200`, to get the *actual* trailing
   200-close window the production code would see (which for this data is
   `closes[50:250]` of the 250-value AAPL2 series, since only the trailing
   200 calendar days are used).
3. Runs both the old and new RSI formulas against that exact window.

Results:

| Symbol | Old (simple-average) RSI | New (Wilder) RSI |
|---|---|---|
| AAPL2 | 64.28571428571423 | **60.83477646733908** |
| JPM | 62.49999999999429 | **60.74766158581481** |

AAPL2's new Wilder RSI (~60.8) is comfortably under the
`rsi_overbought_threshold=70.0` used in this test, so the rejection is
still driven by the golden-cross gate as the test name and design intend —
**not** a fallback RSI-overbought rejection. The test's final assertion
(`JPM` bought, not `AAPL2`) is unaffected and continues to pass.

I updated the stale inline comment in
`tests/test_backtest_commands.py::test_cmd_backtest_run_rejects_death_cross_candidate_for_next_ranked`
to state the new Wilder-computed values (~60.8 for AAPL2, ~60.7 for JPM)
and to note explicitly that RSI stays under the overbought threshold so the
golden-cross gate remains the operative rejection reason. No price-series
data in the test was altered — only the comment.

**Outcome: not blocked.** No BLOCKED/DONE_WITH_CONCERNS condition was
triggered; AAPL2's new RSI did not come out at or above 70.

## Test summary

`python -m pytest tests/ -v` → **232 passed, 0 failed** (after the fix and
comment updates).
