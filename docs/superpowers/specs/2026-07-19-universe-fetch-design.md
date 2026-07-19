# Universe Fetch — Design

Status: Approved for planning
Date: 2026-07-19

## Purpose

Build `universe.py`: the module that produces the daily-cycle's tradeable
candidate list — the top ~100 S&P 500 constituents by market cap, the top
~20 Nasdaq-100 constituents by market cap, and a fixed set of leveraged
full-index funds — ranked by recent volatility so the trading agent can
prioritize names with a wider recent trading range.

This is a sub-scope of the broader "Plan 2" work (universe fetch + Claude
skills + Robinhood MCP wiring) described in
`docs/superpowers/specs/2026-07-18-robinhood-agentic-monthly-trading-design.md`.
Only universe fetching is covered here; the daily-cycle skill and MCP
wiring are separate, later design efforts.

## Background: why this needs its own network layer

Plan 1's `cli.py` is deliberately network-free — Claude fetches live prices
via the Robinhood MCP tools and passes them into CLI commands as arguments.
That pattern doesn't extend to universe fetching: computing "top 100 by
market cap" requires market-cap data for the full ~500-name S&P 500 index,
and volatility ranking requires historical daily bars — both far too much
data to shuttle through the LLM's own tool calls every cycle.

Robinhood's official Agentic Trading MCP does not expose index-membership
or fund-holdings-composition data (confirmed by research: the documented
tool set is `get_accounts`, `get_portfolio`, `get_equity_positions`,
`get_equity_quotes`, `get_equity_orders`, `place_equity_order`,
`cancel_equity_order`, plus watchlists and a "popular stocks/movers" tool —
nothing that returns "current S&P 500 constituents" or "what SPY holds").
So `universe.py` is a second, isolated network-capable component: it does
its own fetching via `yfinance` and a public index-membership source,
entirely separate from the network-free `cli.py` commands built in Plan 1.

## Goals

- Produce a ranked candidate list: top ~100 S&P 500 + top ~20 Nasdaq-100 (by
  market cap) + a fixed leveraged-fund list, annotated with recent
  volatility.
- Keep the daily cycle fast: only recompute the expensive, ~500-ticker
  market-cap sweep when a cache goes stale (not every day); recompute
  volatility fresh every call, but only against the already-narrowed
  ~123-ticker candidate list.
- Use established, well-understood volatility metrics, not a bespoke
  formula.
- Keep every network-touching function isolated behind an injectable
  fetcher/client so the ranking math is unit-testable with zero network
  calls.

## Non-goals

- The daily-cycle SKILL.md, the stop-loss-sweep SKILL.md, and any
  Robinhood MCP tool usage — separate design work.
- Wiring `universe.py`'s output into `cmd_risk_check`/`cmd_record_fill` —
  those already accept prices/values as arguments and don't need to know
  where candidates came from.
- Exact numeric defaults (lookback windows, cache max-age, top-N counts) —
  configurable, tuned together before relying on this in the live daily
  cycle, not architectural decisions.

## Architecture

Two tiers, reflecting how often each kind of data actually changes:

**Tier 1 — Membership + market-cap ranking (cached, refreshed only when
stale).**
1. Scrape the "List of S&P 500 companies" and "Nasdaq-100" tables from
   Wikipedia via `pandas.read_html` to get current constituent tickers.
2. Fetch market cap per ticker via `yfinance`.
3. Rank and take the top ~100 (S&P) / top ~20 (Nasdaq) by market cap.
4. Cache the result plus a timestamp to `data/universe_cache.json`
   (gitignored, same pattern as `ledger.json`).
5. On each call, only re-run steps 1-4 if the cache is missing or older
   than a configurable max-age (default 7 days), or if a refresh is
   explicitly requested. Otherwise reuse the cached list.

**Tier 2 — Volatility ranking (fresh every call).**
Against the cached top-100/top-20 list plus the fixed leveraged-fund list
(`TQQQ`, `UPRO`, `SOXL` — ~123 tickers total, never the full ~500), fetch
recent daily bars via `yfinance` and compute:
- **Annualized realized volatility**: stdev of daily log returns over a
  lookback window (default 20 trading days), annualized (× √252).
- **ATR%**: Average True Range over a lookback window (default 14
  trading days), expressed as a % of price.

A config setting (`realized_vol` | `atr_pct` | `both`) selects which
metric ranks the list; `both` averages each ticker's percentile rank
across the two metrics.

## Components

```
robinhood_bot/
  universe.py            # membership scrape, market-cap ranking,
                          # volatility computation, caching, ranking
  config.py               # extended with UniverseConfig: top_n_sp500,
                           # top_n_nasdaq100, leveraged_funds,
                           # realized_vol_window_days, atr_window_days,
                           # cache_max_age_days, ranking_mode
data/
  universe_cache.json      # cached membership + market-cap ranking + a
                            # timestamp (gitignored)
tests/
  test_universe.py
```

Every network-touching function (Wikipedia table fetch, market-cap fetch,
historical-bars fetch) takes an injectable fetcher/client parameter, so
tests exercise the ranking/caching logic against fixture data with zero
network calls — the same isolation principle Plan 1 used to keep
`risk_engine.py` pure, applied here to separate I/O from the math that
actually needs correctness testing.

## CLI

`cli.py universe [--refresh] [--mode realized_vol|atr_pct|both]` — added
as a fifth subcommand alongside Plan 1's `state`, `risk-check`,
`record-fill`, `check-stop-losses`. Returns a JSON list of:

```json
{
  "symbol": "...",
  "category": "sp500 | nasdaq100 | leveraged",
  "market_cap": 0.0,
  "realized_vol": 0.0,
  "atr_pct": 0.0,
  "combined_rank": 0
}
```

Sourced from the Tier 1 cache unless it's stale or `--refresh` is passed;
Tier 2 volatility is always recomputed fresh.

## Error Handling

- If the Wikipedia scrape fails (network error, unexpected table
  structure) and a cache already exists, fall back to the existing cache
  rather than failing the whole command — log/report the fetch failure,
  don't block the daily cycle over a stale-but-present cache.
- If the Wikipedia scrape fails and there is no existing cache at all,
  the command fails loudly rather than returning a fabricated or partial
  list (consistent with Plan 1's "never fabricate data" rule).
- A ticker whose historical bars can't be fetched for volatility
  computation is dropped from the ranked output for that call, not given
  an estimated/default volatility score.

## Testing Strategy

- **Unit tests (`pytest`, no network):** ranking math (top-N by market
  cap, realized volatility formula, ATR% formula, combined percentile-rank
  averaging), cache staleness logic (missing / fresh / stale / forced
  refresh), and the network-failure fallback behavior — all driven through
  injected fake fetchers returning fixture data.
- **Manual verification (local, once implemented):** run `cli.py universe
  --refresh` for real once, by hand, to confirm the actual Wikipedia
  scrape and yfinance calls work end-to-end; subsequent automated test
  runs never repeat that live call.

## Open Items for Follow-up (not blocking this spec)

- Exact values: `top_n_sp500` (~100), `top_n_nasdaq100` (~20),
  `realized_vol_window_days` (~20), `atr_window_days` (~14),
  `cache_max_age_days` (~7) — config defaults, tuned together later.
- Which specific day/time the weekly Tier 1 refresh actually runs is a
  scheduling concern for the daily-cycle routine design (separate future
  work), not something `universe.py` itself needs to know — it only knows
  "is my cache stale," not "what day is it in the trading calendar."
