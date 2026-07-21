# Scan-Based Universe — Design

Status: Approved for planning
Date: 2026-07-21

## Purpose

Replace `universe.py`/`universe_client.py`'s Wikipedia + `yfinance`-based
candidate universe (S&P 500 + Nasdaq-100 membership, ranked by market cap
then volatility) with a single saved Robinhood scan, so universe building
no longer depends on `yfinance`/Yahoo Finance at all — neither for
membership, market cap, sector, nor per-candidate technical data.

## Background: why this needs to change

The daily-cycle skill now runs from a scheduled cloud routine (see
`docs/superpowers/plans/2026-07-19-backtesting-implementation.md`'s
sibling work on live/paper execution) in a sandboxed cloud environment.
`yfinance` calls to Yahoo Finance from that environment return 403 —
Yahoo's anti-bot defenses block requests from recognized cloud/datacenter
IP ranges, independent of the sandbox's own network egress policy (which
can be, and was, opened up without resolving the 403). This is a known,
persistent limitation of `yfinance` from any cloud sandbox or CI runner,
not something fixable via network configuration on either side.

Robinhood's Agentic Trading MCP does not expose "S&P 500 constituents" or
similar index-membership data (confirmed in the original
`2026-07-19-universe-fetch-design.md`), but it does expose a **scanner**
(`create_scan`, `run_scan`, `update_scan_filters`, `update_scan_config`,
`get_scanner_filter_specs`) capable of filtering the whole market by
fundamental, price/volume, and technical criteria, plus `get_financials`
for period-over-period revenue/income data. Together these can replace
the entire membership-and-ranking pipeline with real-time, agent-driven
calls — consistent with this project's established pattern elsewhere
(Claude fetches live data via MCP tools and passes it into network-free
`cli.py` commands, rather than `cli.py` doing its own network I/O).

## Goals

- Produce the daily cycle's candidate list without any `yfinance`/Yahoo
  Finance dependency, so it works identically whether the cycle runs
  locally or from a cloud routine.
- Replace "top ~100 S&P 500 + top ~20 Nasdaq-100 by market cap, ranked by
  volatility" with: large-cap + liquid (scan filter) → ranked by a
  percentile-averaged blend of daily % change and RSI → gated by a
  YoY revenue-growth quality filter — reflecting a deliberate shift from
  a volatility-seeking philosophy to a momentum-quality one.
- Keep the percentile-rank/averaging math in tested Python, not agent
  arithmetic — only the *fetching* moves to the agent.
- Preserve today's downstream contract: Steps 3-7 of
  `.claude/skills/robinhood-trading/SKILL.md` still consume a ranked
  `Candidate` list with `symbol`, `category`, `sector`, `rsi`,
  `ma_trend_bullish`, `golden_cross_bullish`, `combined_rank`.

## Non-goals

- Changing the sector-concentration limit's mechanics, the stop-loss/
  profit-goal machinery, or anything in `risk_engine.py` — only where the
  candidate list comes from changes, not how it's used.
- Changing backtesting's day-by-day ranking.
  `backtest_commands.py`'s `rank_candidates_as_of` already has its own,
  separate ranking logic driven by `HistoricalPriceStore` (not
  `universe.py`/`universe_client.py`), and backtests always run locally
  where Yahoo's cloud-IP blocking never applied — that function is
  untouched by this design. `universe_client.py`'s
  `LiveHistoricalDataFetcher` (used only by backtesting) is likewise
  unaffected. This is distinct from `backtest run`'s one-time initial
  candidate-list fetch, which *does* currently call the code this design
  deletes — see Open Items.
- A mechanical "recent negative news" check. No Robinhood MCP tool
  exposes news/headlines/sentiment data. This stays a discretionary
  consideration in Step 6, exactly as today, extended in scope to cover
  shortlisted candidates as well as held positions.
- Re-deriving `ma_trend_bullish`/`golden_cross_bullish` from a different
  data source (e.g. scan-provided EMA columns). These keep using
  `get_equity_historicals` + the existing, unchanged
  `is_bullish_ma_trend` function — only membership/ranking/growth-gating
  move to the scan.

## Architecture

**One-time setup (not repeated per cycle):** a saved Robinhood scan,
created via `create_scan` and configured via `update_scan_filters`:

- `FILTER_TYPE_INSTRUMENT_TYPE = stock`
- `FILTER_TYPE_MARKET_CAP > 10,000,000,000` (large-cap floor, replacing
  index membership as the "well-established" bar)
- `FILTER_TYPE_AVERAGE_VOLUME > 1,000,000` (`length`/`interval` tuned to
  a ~10-day average) — a liquidity floor so thin-volume large caps don't
  surface
- `FILTER_TYPE_PERCENT_CHANGE_FROM_CLOSE` and `FILTER_TYPE_RSI` (length
  14, interval `1d`) added across their full valid range — not
  restrictive filters, just there to surface `% Change` and `RSI` as
  result columns
- `FILTER_TYPE_SECTOR` present as a column the same way, for the
  concentration-limit check downstream

The resulting `scan_id` is saved as a config value (not re-created each
run). `run_scan` results are always real-time ("not cached" per the
tool's own description), so there is no membership/market-cap cache to
maintain, unlike the two-tier system this replaces.

**Each cycle** (replaces Step 2 of `robinhood-trading/SKILL.md`):

1. `run_scan(scan_id)` → rows of `{symbol, market_cap, avg_volume,
   pct_change, rsi, sector}`.
2. `cli.py universe rank --scan-rows-json '<rows>'` — Python
   percentile-ranks `pct_change` and `rsi` across all returned rows,
   averages them into `combined_rank`, returns the full list sorted
   descending. (Kept in Python, not agent arithmetic, since this can be
   a few hundred rows.)
3. Agent walks that sorted list top-down. For each candidate, in batches
   of up to 20 (the `get_financials` per-call limit), `get_financials`
   (quarterly, `limit` ~5-8 periods) → compute YoY revenue growth
   (`(revenue_this_quarter - revenue_year_ago) / revenue_year_ago`).
   Drop any candidate with negative or flat growth; keep walking until
   20 survivors are collected or the list is exhausted.
4. Append the 2 fixed leveraged funds (`TQQQ`, `UPRO`) unconditionally —
   never subject to the scan filters or the growth gate, exactly as
   today. Each gets a fixed neutral `combined_rank` of `0.5` rather than
   competing on the scanned stocks' percentile scale, since their
   inclusion is a strategic given, not an earned rank.
5. For all ~22 finalists: `get_equity_historicals` (same pattern already
   used for held positions in Step 4) → build a `symbol: [closes]`
   object.
6. `cli.py universe finalize --candidates-json '<22 finalists>'
   --closes-json '<historicals>'` — Python attaches
   `ma_trend_bullish`/`golden_cross_bullish` via the existing
   `is_bullish_ma_trend`, returns the final ranked `Candidate` list in
   the same shape Steps 3-7 already expect.

## Components

**`robinhood_bot/universe.py`**
- Remove: `UniverseCache`, `SectorCache`, `CachedMember`,
  `load_cache`/`save_cache`, `load_sector_cache`/`save_sector_cache`,
  `is_cache_stale`, `refresh_membership`, `get_membership`, `get_sector`,
  `rank_top_by_market_cap`, `realized_volatility`,
  `average_true_range_pct`, `build_universe`, the `MarketDataClient`
  protocol, and `Bar` — none of this is needed once there's no
  membership cache and no market-cap/volatility fetch to do.
- Keep unchanged: `Candidate` (fields adjusted — `pct_change`/`rsi`
  replace `realized_vol`/`atr_pct` as the ranking-basis fields),
  `relative_strength_index`, `is_bullish_ma_trend`, `percentile_ranks`,
  `UniverseConfig` (fields adjusted to match: drop
  `top_n_sp500`/`top_n_nasdaq100`/`cache_max_age_days`/`ranking_mode`,
  keep the RSI/MA/golden-cross window settings).
- Add: `rank_by_scan(scan_rows: list[dict], cfg) -> list[dict]` (the
  percentile-rank + averaging step) and `finalize_candidates(rows:
  list[dict], closes_by_symbol: dict, cfg) -> list[Candidate]` (the
  MA-trend/golden-cross attachment step). `finalize_candidates` only
  attaches those two fields — it carries `combined_rank` (and every
  other field) through from its input unchanged, and does not re-sort or
  re-rank; ranking is entirely `rank_by_scan`'s responsibility.

**`robinhood_bot/universe_client.py`**
- Delete `LiveMarketDataClient` and the two Wikipedia URL constants
  entirely (dead code once nothing calls `fetch_sp500_tickers`/
  `fetch_nasdaq100_tickers`/`fetch_market_caps`/`fetch_sector`/
  `fetch_daily_bars`).
- Keep `LiveHistoricalDataFetcher` unchanged (backtesting-only).

**`robinhood_bot/cli.py`**
- Replace the single `universe [--refresh] [--mode ...]` subcommand with
  two: `universe rank --scan-rows-json ...` and `universe finalize
  --candidates-json ... --closes-json ...`.
- Remove `UNIVERSE_CACHE_PATH`, `SECTOR_CACHE_PATH`, and the
  `build_universe(LiveMarketDataClient(), ...)` call in
  `_dispatch_backtest`'s `run` branch — `backtest run` sources its
  candidate list from `cli.py universe`'s output today; this needs its
  own decision (see Open Items).

**`.claude/skills/robinhood-trading/SKILL.md`**
- Step 2 rewritten to the 6-step sequence above.
- Step 3 (build today's research shortlist) is otherwise unchanged — it
  already just takes the top of `combined_rank` plus held positions.

## Error Handling

- `run_scan` fails or returns zero rows: no fallback cache exists
  anymore. Skip new BUY consideration for this cycle entirely, report it
  plainly in the Step 9 summary. Held-position management (stop-loss
  sweep, profit-goal exits) is unaffected, since it doesn't depend on the
  candidate universe.
- `get_financials` fails for a candidate during growth-filtering: drop
  that candidate (can't confirm it clears the quality gate) and continue
  to the next-ranked one — same "never fabricate, skip on failure"
  precedent as a failed quote elsewhere in this skill.
- `get_equity_historicals` fails, or returns too little history, for a
  finalist: leave `ma_trend_bullish`/`golden_cross_bullish` as `null` for
  that symbol rather than dropping it from the list — identical to the
  existing rule for held positions ("omit the symbol from the closes
  object... risk-check skips the check rather than blocking on missing
  data").
- Fewer than 20 candidates survive filtering: proceed with however many
  there are — "top ~20" was already an approximate cap before this
  change, not a hard requirement.

## Testing Strategy

- Unit tests (pure, no network): `rank_by_scan` (percentile-rank +
  averaging math, sort order, ties) and `finalize_candidates`
  (MA-trend/golden-cross attachment; missing-closes → `null` fallback;
  leveraged-fund fixed `combined_rank` handling) — fixture-driven, same
  isolation principle the codebase already uses throughout.
- Delete the now-obsolete cache/membership/sector-cache tests in
  `test_universe.py` (staleness logic, Wikipedia-fallback behavior) along
  with the code they tested.
- No pytest coverage for the `run_scan`/`get_financials`/
  `get_equity_historicals` calls themselves — those are agent-orchestrated
  MCP calls, the same testing boundary as Step 4's live quote-fetching
  today.
- Manual verification once implemented: create the real scan, run one
  live cycle by hand, confirm data actually flows end-to-end through
  `run_scan` → `rank` → `get_financials` → `get_equity_historicals` →
  `finalize`.

## Open Items for Follow-up (not blocking this spec)

- `backtest run`'s candidate sourcing: today it calls
  `build_universe(LiveMarketDataClient(), ...)` once at the start of a
  backtest run to get "today's live universe, applied retroactively."
  Once `build_universe`/`LiveMarketDataClient` are deleted, `backtest
  run` needs a different candidate source — likely a fixed symbol list
  passed in, or a one-time scan snapshot. Deliberately deferred to the
  implementation plan rather than decided here, since it's a backtesting
  concern, not a live/paper universe-building one.
- Exact values (`market_cap` floor of $10B, `average_volume` floor of 1M
  over 10 days, growth-filter lookback of 5-8 quarters, buffer size of
  ~40-50 before growth-filtering) are initial defaults to tune once
  running for real, consistent with how the original design's numeric
  defaults were treated.
- The saved scan itself (its `scan_id`, exact filter configuration) is
  created once, manually, as part of implementing this — not something
  `cli.py` or the skill creates programmatically.
