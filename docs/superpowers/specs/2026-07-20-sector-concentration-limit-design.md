# Sector Concentration Limit — Design

Status: Approved for planning
Date: 2026-07-20

## Purpose

The 3-week LLM-driven backtest run in this session's transcript surfaced a
real gap: the volatility-ranking strategy naturally clusters entries in
whichever sector currently has the most volatile names (in that run,
semiconductors/storage — SNDK, GLW, WDC, KLAC, STX, MRVL, MU, AMAT, LRCX
all showed up as top-ranked candidates in the same window), so a single
sector-wide selloff hit most of the "diversified" 5-slot portfolio at
once. This adds a hard, mechanical limit — at most one active/waiting
position per sector at a time — enforced identically in live/paper
trading and the deterministic backtest, consistent with this project's
existing principle that backtesting exercises the real risk logic, never
an approximation of it.

## Background: what already exists and doesn't need to change

`rank_candidates_as_of` (daily volatility re-ranking) and the entries
loop's "try candidates in rank order, skip on rejection" structure in
`backtest_commands.cmd_backtest_run` are unchanged — this feature adds
one more rejection reason to `evaluate_buy`, which the existing loop
already handles by moving to the next-ranked candidate. No re-ranking or
upfront diversity filtering is introduced.

`universe.py`'s existing `category` field (`"sp500"` / `"nasdaq100"` /
`"leveraged"`) is index membership, not sector/industry classification,
and is unrelated to this feature — both fields coexist on `Candidate`.

## Non-goals

- No concentration limit on `LONG_HOLD` positions — once promoted, a
  position's sector frees up for a new active entry, matching how
  `LONG_HOLD` already doesn't count against the active-slot cap.
- No concentration limit on leveraged funds (`TQQQ`/`UPRO`/`SOXL`) —
  `yfinance` doesn't classify index-tracking ETFs into a GICS sector the
  same way it does individual stocks, and these are already a separate,
  specially-handled `category="leveraged"` bucket in this codebase. A
  future enhancement could add a similar cap for leveraged-fund count,
  but that's out of scope here.
- No re-ranking of candidates by sector diversity ahead of time — this
  is purely a per-candidate gate at `evaluate_buy`, relying on the
  existing ranked-list iteration to skip rejected candidates.
- `max_positions_per_sector` is a tunable `RiskConfig` default, not a
  runtime CLI flag — matches how every other risk threshold in this
  project is only adjustable by editing the code default.
- No handling for a company's real-world sector classification changing
  over time — `data/sector_cache.json` never expires once a symbol is
  cached, an accepted simplification for data that essentially never
  changes.

## Architecture

### Data flow

Sector is treated as a **permanent fact about a symbol**, resolved once
and then carried along wherever it's needed — never re-fetched on every
risk-check call:

- A **new candidate's** sector comes from `cli.py universe`'s output
  (`Candidate.sector`, resolved during `build_universe`).
- An **already-held position's** sector comes from `cli.py state`'s
  output (`Position.sector`, captured once at buy time and persisted in
  `ledger.json`).

This means no new "look up a symbol's sector" CLI command is needed —
both the live daily-cycle skill and the LLM-driven backtest mode already
call `universe`/`state` for other reasons and get sector data for free.

### `universe.py` changes

```
MarketDataClient (Protocol)
  + fetch_sector(ticker: str) -> str | None

Candidate (dataclass)
  + sector: str | None

SectorCache (new dataclass)
  sectors: dict[str, str]   # symbol -> sector, permanent (no fetched_at/staleness)

load_sector_cache(path) -> SectorCache | None
save_sector_cache(path, cache) -> None
get_sector(client, cache_path, symbol) -> str | None
  # cache-hit returns immediately; cache-miss fetches via client,
  # persists on success, returns None on failure — never fabricated.

build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False) -> list[Candidate]
  # for each non-leveraged member: resolve sector via get_sector;
  # if None, DROP the candidate entirely (same precedent as the
  # existing "no bars data" drop). Leveraged funds get sector=None
  # hardcoded, never dropped, never sector-checked downstream.
```

### `universe_client.py` changes

```python
class LiveMarketDataClient:
    def fetch_sector(self, ticker: str) -> str | None:
        try:
            info = yf.Ticker(ticker).info
            sector = info.get("sector")
        except Exception:
            return None
        return sector if sector else None
```

Matches the existing `fetch_market_caps` try/except pattern. This uses
`yfinance`'s slower `.info` property (not the `.fast_info` already used
for market cap), verified manually like every other `LiveMarketDataClient`
network method in this codebase.

### `portfolio_state.py` / `ledger.py` changes

```
Position (dataclass)
  + sector: str | None = None   # defaulted — Position is constructed
                                  # positionally throughout the existing
                                  # test suite; a required field would
                                  # break dozens of call sites.
```

`ledger.py`'s `_position_to_dict`/`_position_from_dict` persist/read
`sector` the same backward-compatible way as every other field added
this session (`.get("sector")`, defaulting to `None` for old ledger
files).

### `risk_engine.py` changes

```
RiskConfig
  + max_positions_per_sector: int = 1

evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector: str | None) -> BuyDecision
  # New check, placed right after the existing is_held check:
  if sector is not None:
      sector_count = sum(1 for p in state.active_positions if p.sector == sector)
      if sector_count >= cfg.max_positions_per_sector:
          return BuyDecision(False, f"sector concentration: already at the "
                              f"{cfg.max_positions_per_sector}-position limit "
                              f"for {sector}", max_value)
```

`sector` is a **required** parameter (unlike the defaulted `cmd_*`-level
plumbing below) — `evaluate_buy` has only two real call sites in
production code (`cmd_risk_check`'s buy branch, `cmd_backtest_run`'s
entries loop), small enough to make the new input explicit everywhere
rather than silently defaulted. Scanning `state.active_positions`
naturally covers both `ACTIVE` and `WAITING` sub-statuses, since a
`WAITING` position hasn't been promoted to `long_hold_positions` yet —
exactly the intended scope.

### `commands.py` changes

```
cmd_risk_check(..., sector: str | None = None) -> dict
  # passed through to evaluate_buy on the "buy" branch; irrelevant to "sell"

cmd_record_fill(..., sector: str | None = None) -> dict
  # persisted onto the newly created Position on the "buy" branch
```

Both defaulted (unlike `evaluate_buy` itself) since `sell` never needs a
sector, and this keeps the many existing sell-path tests unaffected.

### `backtest_commands.py` changes

```
cmd_backtest_risk_check(..., sector: str | None = None) -> dict
cmd_backtest_record_fill(..., sector: str | None = None) -> dict
  # thin pass-through, same as every other backtest_* wrapper

cmd_backtest_run(..., candidate_sectors: dict[str, str]) -> dict
  # new required parameter alongside the existing candidate_symbols list.
  # In the entries loop: evaluate_buy(..., candidate_sectors.get(symbol))
  # and the subsequent cmd_record_fill(..., sector=candidate_sectors.get(symbol)).
  # No change to rank_candidates_as_of or the loop's control flow.
```

### `cli.py` changes

- New `SECTOR_CACHE_PATH = Path("data/sector_cache.json")` constant.
- Live `universe` dispatch passes `SECTOR_CACHE_PATH` into `build_universe`;
  output dict per-candidate gains `"sector": c.sector`.
- Live `risk-check`/`record-fill` (buy) and their backtest equivalents
  gain a new optional `--sector` flag, threaded through to the
  corresponding `cmd_*` function.
- `_dispatch_backtest`'s `"run"` case builds
  `candidate_sectors = {c.symbol: c.sector for c in candidates if c.sector is not None}`
  from the same `build_universe` call already used for `candidate_symbols`,
  and passes it into `cmd_backtest_run`.

## SKILL.md updates

- `robinhood-trading/SKILL.md`: Step 2 (universe) notes `sector` is now
  part of each candidate's data; Step 7 notes the new `--sector` flag
  needed for buy risk-checks/fills, sourced from Step 2's candidate data
  for new entries (no new fetch step needed).
- Backtest Mode section: the same initial `cli.py universe` call
  (already used to build the fixed candidate list) now also carries
  sector data — no new command needed there either.

## Testing Strategy

- **`universe.py`:** `SectorCache` load/save round-trip; `get_sector`
  cache-hit / cache-miss-and-fetch / fetch-failure-returns-None; `build_universe`
  drops a candidate with unresolvable sector (mirroring the existing
  "drops symbols with no bars" test); `build_universe` sets `sector=None`
  for leveraged funds without dropping them; a resolvable candidate's
  `sector` appears correctly in the returned `Candidate`.
- **`universe_client.py`:** `fetch_sector` is real-network-touching, no
  automated test — verified manually once, like every other
  `LiveMarketDataClient` method.
- **`portfolio_state.py` / `ledger.py`:** round-trip persistence test for
  `Position.sector`, backward-compatible default for old ledger files.
- **`risk_engine.py`:** `evaluate_buy` tests for the three new cases
  (rejected — sector already held; approved — different sector; approved
  — `sector=None`), plus updating every existing `evaluate_buy` test call
  with the new required argument.
- **`commands.py` / `backtest_commands.py`:** `cmd_risk_check`/
  `cmd_record_fill` tests for the new optional `sector` parameter
  (rejection reason, persistence onto the new `Position`).
- **`backtest_commands.py`:** a new hand-verified `cmd_backtest_run`
  scenario where a same-sector candidate is correctly skipped in the
  entries loop in favor of the next-ranked, different-sector candidate —
  proving the wiring, not just the pure-function logic.
- **`cli.py`:** new `--sector` flag tests on `risk-check`/`record-fill`
  (live and backtest); `universe` command's output includes `"sector"`
  per candidate.

## Error Handling

- A sector that can't be resolved for a regular (non-leveraged) stock
  candidate: that candidate is dropped from `build_universe`'s output
  entirely — never fabricated, matching the existing "no bars data" rule.
- Leveraged funds always have `sector=None` by design, not as a failure
  case, and are exempt from the concentration rule.
- `data/sector_cache.json` never expires once a symbol is cached — a
  real-world sector reclassification would require manually clearing the
  cache file (accepted limitation, noted above under Non-goals).

## Open Items for Follow-up (not blocking this spec)

- Whether `max_positions_per_sector`'s default of 1 is right in practice
  once observed over more backtests — left as a `RiskConfig` default to
  tune later, same as every other threshold in this file.
- Whether leveraged funds eventually warrant their own concentration
  rule (e.g. capping total leveraged-fund exposure) — not requested, not
  implemented here.
