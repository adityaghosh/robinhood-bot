# Backtesting via Paper Trading — Design

Status: Approved for planning
Date: 2026-07-19

## Purpose

Before connecting Robinhood's Agentic Trading MCP, validate the trading
strategy against historical data using the same paper-trading engine
(ledger, risk limits, position lifecycle) that will run for real once
connected. Two complementary modes:

1. **Deterministic backtest** — a fast, fully-automated, reproducible
   Python loop over an arbitrary historical date range, using a fixed
   rule in place of Claude's judgment. Can run months or years of
   history in seconds.
2. **LLM-driven backtest** — the actual daily-cycle skill's research and
   decision logic, applied day by day over historical data instead of
   live Robinhood MCP quotes. Tests the real judgment the bot will use,
   over a realistically short window (weeks, not years) given the cost
   of one reasoning pass per simulated day.

Both modes reuse the exact same ledger/risk-check/record-fill machinery
already built and tested — this is "paper trading" against the past
instead of the present, not a separate accounting system.

## Background: what already exists and doesn't need to change

`ledger.load_state(path, starting_cash)` / `save_state(path, state)`
already accept an arbitrary path. `commands.cmd_state`,
`cmd_risk_check`, `cmd_record_fill`, and `cmd_check_stop_losses` already
accept a `ledger_path` and (where relevant) a `today: date` as explicit
parameters — none of this is hardcoded to "now" or to the live ledger
file. `risk_engine.py` is already pure and deterministic, taking
`today`/prices explicitly. Nothing in the core engine needs to change
for backtesting to work; the work here is new orchestration and data
around it.

## Non-goals

- Point-in-time historical index membership (which stocks were actually
  in the S&P 500/Nasdaq-100 on a given past date) — free data sources
  don't provide this. The backtest trades within *today's* current
  `cli.py universe` candidate list, applied retroactively across the
  whole historical window. This has survivorship bias (today's winners
  are overrepresented relative to what was really tradeable back then)
  and is explicitly accepted as a simplification for validating
  strategy/risk logic, not for precise historical return claims.
- Multiple pluggable deterministic strategies — one fixed rule (top
  volatility-ranked candidate fills each free slot) for now.
- Scheduled/automated invocation of either backtest mode.
- Options/crypto/futures backtesting — equities only, matching the
  live bot's current scope.
- Same-day re-entry into a symbol that was just sold (if it's still
  top-ranked) is possible and not specially prevented — the
  deterministic rule has no cooldown period.

## Architecture

A structurally separate `cli.py backtest ...` command group, so the
live commands (`state`, `risk-check`, `record-fill`, `check-stop-losses`,
`universe`) are never touched by this work and carry zero risk of a
stray flag pointing a real invocation at backtest data. Every backtest
command internally reuses the existing `commands.py` functions —
resolving a run identifier to an isolated ledger path and forwarding to
`cmd_state`/`cmd_risk_check`/`cmd_record_fill`/`cmd_check_stop_losses`
unchanged, with `trading_mode="backtest"` as a third value alongside
`"paper"`/`"live"`.

```
robinhood_bot/
  backtest_data.py       # HistoricalPriceStore: fetches + caches full
                          # OHLC for a symbol over an arbitrary date
                          # range (shared cache, independent of any run);
                          # no-lookahead close/rolling-window lookups;
                          # derives the trading-day calendar from a
                          # benchmark symbol's own historical dates
  backtest_commands.py    # cmd_backtest_state/quote/risk_check/
                           # record_fill/check_stop_losses (thin
                           # wrappers around commands.py, resolving
                           # --run to an isolated ledger path);
                           # cmd_backtest_run (deterministic loop);
                           # cmd_backtest_report (equity/trades/benchmark)
  cli.py                   # gains a `backtest` subcommand group
data/backtests/<run_id>/
  ledger.json                # isolated portfolio state for this run
  trade_log.csv               # isolated trade log for this run
data/historical_price_cache/  # shared, run-independent OHLC cache
tests/
  test_backtest_data.py
  test_backtest_commands.py
```

## CLI Surface

```
cli.py backtest state --run RUN_ID --asof DATE
cli.py backtest quote SYMBOL --asof DATE
cli.py backtest risk-check {buy|sell} SYMBOL --run RUN_ID --asof DATE [--value] [--prices-json]
cli.py backtest record-fill {buy|sell} SYMBOL --run RUN_ID --asof DATE --qty --price [--reason]
cli.py backtest check-stop-losses --run RUN_ID --asof DATE --prices-json [--apply]
cli.py backtest run --run RUN_ID --start DATE --end DATE
cli.py backtest report --run RUN_ID
cli.py backtest trading-days --start DATE --end DATE
```

`backtest trading-days` returns the list of actual trading dates in a
range (derived from a benchmark symbol's own historical dates, see
below) — used by the LLM-driven mode to know which dates to iterate,
rather than guessing which calendar dates are trading days.

`--run RUN_ID` resolves to `data/backtests/<run_id>/` for that run's
ledger and trade log — grouping a run's artifacts together and making
it impossible to pass a raw path that collides with the live
`data/ledger.json`. `backtest quote` has no `--run` since historical
price data isn't run-specific; only portfolio state is.

## Historical Price Data (`backtest_data.py`)

`HistoricalPriceStore` fetches full daily OHLC for a symbol over
`[start, end]` via `yfinance` (extending the same library already used
in `universe_client.py`, via `yf.Ticker(symbol).history(start=, end=)`),
caching to `data/historical_price_cache/<symbol>.json` so overlapping
backtest runs don't re-fetch. Exposes:

- `get_close(symbol, date) -> float | None`
- `get_ohlc(symbol, date) -> Bar | None`
- `get_closes_window(symbol, end_date, window_days) -> list[float]` —
  trailing closes ending at (and including) `end_date`, never anything
  past it. This is what makes daily re-ranking possible without leaking
  future volatility into past decisions.
- `trading_days(start, end) -> list[date]` — derived from a benchmark
  symbol's (e.g. SPY's) own historical date index, rather than a
  separate market-calendar dependency, so weekends/holidays are
  naturally excluded.

A missing/failed fetch for a symbol is never fabricated — that symbol
is simply unavailable for the affected date(s), consistent with the
"never fabricate a price" rule used everywhere else in this bot.

## Deterministic Backtest (`cli.py backtest run`)

For each trading day in `[start, end]`, in order:

1. **Exits.** For each `ACTIVE`/`WAITING` position in the run's ledger,
   evaluate against that day's close via the same `risk_engine.
   evaluate_position` logic `check-stop-losses` uses, and apply the
   result (`SELL` → recorded as a fill; `PROMOTE_LONG_HOLD` → applied to
   the ledger; `HOLD` → nothing).
2. **Entries.** For each free active slot: take today's fixed universe
   candidate list, rank it by `combined_rank` recomputed *as of this
   simulated day* (realized volatility / ATR% computed from
   `get_closes_window`/`get_ohlc` ending on this day, never later —
   this is the no-lookahead guarantee), and propose the top-ranked
   candidate not already held. Gate it through the same `evaluate_buy`
   logic `risk-check` uses; if approved, record the fill.
3. Advance to the next trading day.

This is a pure Python loop — no subprocess calls to the CLI, no LLM
involvement — so it can run a multi-year window in seconds.

## LLM-Driven Backtest (Backtest Mode on `robinhood-trading` skill)

A new "Backtest Mode" section added to the existing
`.claude/skills/robinhood-trading/SKILL.md`, not a separate skill file.
Invoked with a date range and run id (e.g. `/robinhood-trading --backtest
--run RUN_ID --start DATE --end DATE`). The research and decision logic
(shortlisting, research, proposing trades) is unchanged from live mode —
only the data sources differ:

- Step 1 (read mode & holdings) reads `cli.py backtest state --run
  RUN_ID --asof <simulated date>` instead of live `cli.py state`.
- Step 4 (fresh quotes) reads `cli.py backtest quote SYMBOL --asof
  <simulated date>` instead of the Robinhood MCP quote tool — meaning
  this mode needs no MCP connection at all, consistent with using it
  *before* MCP is connected.
- Steps 7-8 (gate and execute) use `cli.py backtest risk-check`/
  `record-fill --run RUN_ID --asof <simulated date>` in place of the
  live equivalents; there is no live-order-placement branch in backtest
  mode — everything is simulated, always.

Claude loops through each trading day in the requested range itself,
within one session, calling `cli.py backtest trading-days --start --end`
(or reusing `HistoricalPriceStore.trading_days` via a small CLI
exposure) to get the list of simulated dates to iterate, rather than
guessing which calendar dates are trading days.

## Reporting (`cli.py backtest report`)

Reads the run's final ledger state and full trade log, and reports:

- Starting and ending equity, total return %.
- Win/loss counts (profitable vs. unprofitable closed trades, from the
  trade log).
- Max drawdown over the run.
- A buy-and-hold benchmark: SPY's return over the same `[start, end]`
  window, from the same `HistoricalPriceStore`, for context on whether
  the strategy beat just holding the market.

## Testing Strategy

- **Unit tests (`pytest`, no network):** `backtest_data.py`'s window/
  no-lookahead logic and trading-day derivation, driven by an injectable
  fetcher (same isolation pattern `universe.py`/`universe_client.py`
  already use); `backtest_commands.py`'s run-id-to-path resolution and
  its thin forwarding to `commands.py`'s already-tested functions; the
  deterministic strategy's ranking/entry logic against fixture price
  data.
- **Manual verification:** one real `cli.py backtest run` against a
  short live date range (e.g. the last month) to confirm the actual
  `yfinance` historical fetch and caching work end to end — the same
  pattern used to verify `universe_client.py`'s live calls.
- The LLM-driven mode, like the rest of SKILL.md content, has no
  automated test — verified by actually running it once the Python
  side of this spec is built.

## Error Handling

- A historical price fetch failure for a symbol: that symbol is
  unavailable for the affected date(s) — skipped, never fabricated.
- If `trading_days` can't be derived (benchmark symbol fetch fails),
  the backtest command fails loudly rather than guessing at a calendar.
- `backtest risk-check`/`record-fill` fail closed exactly like their
  live counterparts (already-established behavior in `commands.py`,
  unchanged here).

## Open Items for Follow-up (not blocking this spec)

- Exact deterministic-strategy parameters (already governed by the
  existing `RiskConfig` defaults; nothing new to tune here beyond what
  the live bot already has).
- Whether to eventually support point-in-time historical universe
  membership via a paid data source, if backtest accuracy becomes a
  priority beyond validating strategy/risk mechanics.
