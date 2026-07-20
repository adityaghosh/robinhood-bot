# RSI and Moving-Average Crossover Entry/Exit Signals — Design

Status: Approved for planning
Date: 2026-07-20

## Purpose

The universe ranking (`combined_rank`) answers "how volatile/choppy is
this stock" — it's an opportunity-set filter, not a timing signal. Both
LLM-driven backtests run earlier this session bought into strong
momentum names right before a reversal (e.g. GLW, AMAT), because nothing
in the pipeline distinguishes "volatile and worth watching" from "volatile
and priced for a pullback right now." This adds two standard, deterministic
technical indicators — RSI and a short/long moving-average trend check —
computed from price history already being fetched, to gate entries and
inform exits, without touching the volatility ranking itself.

## Background: what already exists and doesn't need to change

- `combined_rank`, `realized_volatility`, `average_true_range_pct`, and
  the `ranking_mode` config in `universe.py` are unchanged — this is an
  additive, independent signal layered on top, not a replacement.
- `rank_candidates_as_of` (backtest day-by-day re-ranking) keeps ranking
  purely by volatility/ATR; RSI/MA only affect whether a candidate that's
  already been ranked and selected is actually approved for a BUY.
- The existing sector-concentration check in `evaluate_buy` establishes
  the exact pattern this follows: a per-candidate signal computed
  upstream, passed into `evaluate_buy`, gating with a clear rejection
  reason, `None`/neutral values bypassing rather than fail-closed.
- The weekly profit-goal mechanism (`evaluate_profit_exits`) and the
  stop-loss/grace-period mechanism (`evaluate_position`) are both
  unchanged — RSI/MA add a new, separate, purely discretionary exit
  signal alongside them, not a third mechanical exit trigger.

## Non-goals

- No other indicators (MACD, Bollinger Bands, etc.) — just RSI and one
  moving-average trend check, matching what was requested.
- No oversold/mean-reversion buy logic — RSI only gates the overbought
  ceiling; there is no RSI floor requirement to buy.
- No discrete crossover-event detection (tracking the exact day the
  short MA crossed the long MA) — just the short MA's current position
  relative to the long MA, which is simpler to compute, test, and reason
  about, and is a reasonable proxy for "in a confirmed short-term uptrend"
  given this bot already only considers already-moving candidates.
- No LLM override of the entry gate — it's mechanical and
  Python-enforced, exactly like every other `evaluate_buy` check. Only
  the exit side is discretionary (explicit user choice).
- No change to `combined_rank` or which candidates appear in the universe
  output at all — RSI/MA never reorder or filter the ranked list itself,
  only whether a specific BUY attempt on an already-selected candidate is
  approved.
- No CLI-tunable thresholds — `rsi_overbought_threshold` and the MA
  windows are code-level config defaults, matching every other risk/
  universe threshold in this project.

## Architecture

### Computation (`universe.py`)

Two new pure functions, alongside `realized_volatility`/
`average_true_range_pct`:

```python
def relative_strength_index(closes: list[float], window_days: int = 14) -> float:
    # Standard RSI: average gain / average loss over the trailing window,
    # converted to the 0-100 RSI scale. Returns a neutral 50.0 when there
    # isn't enough history (mirrors average_true_range_pct's "return 0.0
    # when insufficient" precedent) -- a neutral RSI can never spuriously
    # trigger the overbought rejection below.
    ...

def is_bullish_ma_trend(closes: list[float], short_window: int = 5, long_window: int = 20) -> bool | None:
    # True if the short-window SMA is currently above the long-window SMA
    # ("in a confirmed short-term uptrend"), False if at or below, None if
    # there isn't enough history for the long-window average.
    ...
```

### Config (`universe.py`, `risk_engine.py`)

```
UniverseConfig
  + rsi_window_days: int = 14
  + ma_short_window_days: int = 5
  + ma_long_window_days: int = 20

RiskConfig
  + rsi_overbought_threshold: float = 70.0
```

### Data flow

- `Candidate` (universe.py) gains `rsi: float` and `ma_trend_bullish: bool | None`,
  computed in `build_universe` from the same daily bars already fetched
  for realized_vol/ATR, exactly like `sector` is computed alongside them.
- `Position` (portfolio_state.py) gains `rsi: float | None = None` and
  `ma_trend_bullish: bool | None = None` (both defaulted, backward-compatible,
  matching the `sector` precedent), captured once at buy time and persisted
  via ledger.py's existing `.get(key, default)` pattern.
- Unlike `sector` (a permanent fact about a symbol), RSI/MA trend change
  daily, so they cannot be precomputed once per backtest run the way
  `candidate_sectors` is. `backtest_commands.py`'s entries loop computes
  them fresh per candidate per day from `store.get_closes_window(...)`
  (the same historical-price-store method already used elsewhere),
  immediately before each `evaluate_buy` call.

### Entry gate (`risk_engine.py`)

`evaluate_buy` gains two required parameters, `rsi: float` and
`ma_trend_bullish: bool | None`, with two new checks placed after the
existing sector-concentration check:

```python
if rsi > cfg.rsi_overbought_threshold:
    return BuyDecision(False, f"overbought: RSI {rsi:.1f} exceeds {cfg.rsi_overbought_threshold:.0f}", max_value)

if ma_trend_bullish is False:
    return BuyDecision(False, "no confirmed short-term uptrend (short MA at or below long MA)", max_value)
```

`ma_trend_bullish is False` (not falsy) is deliberate: `None` (insufficient
history) bypasses the check exactly like `sector=None` bypasses the
sector-concentration check — a data gap must never fail closed and block
an otherwise-good trade.

### Wiring (`commands.py`, `backtest_commands.py`, `cli.py`)

- `cmd_risk_check`/`cmd_record_fill` and their backtest wrappers gain
  optional `rsi: float = 50.0` and `ma_trend_bullish: bool | None = None`
  parameters (defaulted, like `sector`), so existing sell-path callers
  and tests are unaffected.
- `cmd_record_fill`'s buy branch persists both onto the newly created
  `Position`.
- `cli.py` gains `--rsi` and `--ma-bullish` (tri-state: pass the flag for
  `True`, omit for `None`/unknown — no explicit "false" flag needed since
  a live/backtest caller either has a confirmed bullish reading or
  doesn't assert one) flags on `risk-check`/`record-fill`, live and
  backtest variants, threaded through the same way `--sector` is.
- Live `universe` dispatch and the backtest `run` dispatch's candidate
  data both include `rsi`/`ma_trend_bullish` per candidate, same as
  `sector` today.

### Exit side (`commands.py`)

`cmd_state`'s per-position summary (`_position_summary`) gains `"rsi"`
and `"ma_trend_bullish"` fields, computed fresh from current price
history at `cmd_state` call time (not read from the stored `Position`
fields, which only reflect the value at buy time) — the LLM needs today's
reading to judge an exit, not the entry-day reading.

### SKILL.md updates

`robinhood-trading/SKILL.md` Step 7 gets the two new mechanical rejection
reasons documented (mirroring the sector-concentration bullet already
there). Step 6's discretionary-exit guidance splits by lifecycle status,
since a bullish/bearish reading means something different depending on
whether a position is still active or already parked in long-hold:

- **ACTIVE/WAITING positions:** consider an early SELL if RSI is deep in
  overbought territory or the short-term trend has turned bearish —
  alongside the existing "moved sharply against you" guidance. Same
  discretionary bucket, not a new mechanical trigger.
- **LONG_HOLD positions:** a bullish MA-trend reading (short SMA back
  above the long SMA) is a specific signal to consider **selling into
  the bounce** rather than holding out for a full recovery — a long-hold
  position has no guaranteed further upside, so a confirmed short-term
  uptrend is often the best exit opportunity available, not a reason to
  wait for more. This is the main new discretionary trigger for
  long-hold positions, which previously only had the weekly profit-goal
  sweep (gain-only) or general "moved against you" guidance (which
  doesn't really apply to something already deep underwater) to go on.

## Testing Strategy

- `universe.py`: `relative_strength_index` unit tests for a known
  all-gains sequence (RSI near 100), a known all-losses sequence (RSI
  near 0), a mixed sequence against a hand-computed expected value, and
  the insufficient-data neutral-50.0 case. `is_bullish_ma_trend` tests
  for a clear bullish case, a clear bearish case, and the insufficient-data
  `None` case.
- `build_universe`: a test confirming `rsi`/`ma_trend_bullish` appear
  correctly on a resolved `Candidate`.
- `risk_engine.py`: `evaluate_buy` tests for the two new rejection cases
  (overbought RSI, bearish MA trend) plus a case proving `ma_trend_bullish=None`
  bypasses the check (approved), plus updating every existing `evaluate_buy`
  test call with the two new required arguments (explicit ripple to flag
  in the plan, same as the sector-concentration rollout).
- `commands.py`/`backtest_commands.py`: tests for the new optional
  `rsi`/`ma_trend_bullish` parameters on `cmd_risk_check`/`cmd_record_fill`
  (rejection reasons, persistence onto the new `Position`), and a
  `cmd_state` test confirming a held position's summary includes fresh
  `rsi`/`ma_trend_bullish` fields.
- `backtest_commands.py`: a hand-verified `cmd_backtest_run` integration
  test where an overbought/bearish-trend candidate is rejected in the
  entries loop in favor of the next-ranked candidate — same shape as the
  sector-concentration integration test from the prior feature.
- `cli.py`: `--rsi`/`--ma-bullish` flag tests on `risk-check`/`record-fill`
  (live and backtest); `universe` output includes both new fields per
  candidate.

## Error Handling

- Insufficient price history for RSI: returns a neutral `50.0`, never a
  fabricated overbought/oversold reading — this can never itself trigger
  the overbought rejection.
- Insufficient price history for the MA trend: returns `None`, which the
  entry gate treats as "can't confirm, don't block" — a data gap can
  never fail closed and block an otherwise-good trade.
- Both indicators are pure functions with no I/O and no external failure
  mode beyond normal arithmetic on the closes list already in hand.

## Open Items for Follow-up (not blocking this spec)

- Whether `rsi_overbought_threshold=70.0` and the 5/20-day MA pairing are
  the right defaults in practice — left as tunable config defaults,
  same as every other threshold in this project, to adjust after
  observing more backtests.
- Whether a discrete crossover-event detector (vs. current-alignment)
  would perform meaningfully differently — explicitly deferred, not
  part of this spec.
