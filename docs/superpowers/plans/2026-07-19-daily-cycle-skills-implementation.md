# Daily Trading Cycle & Stop-Loss Sweep Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `TRADING_MODE` field to `cli.py state`'s output (so both skills can read the paper/live switch programmatically), then author the two Claude Code skills — `robinhood-trading` (daily research-and-trade cycle) and `robinhood-stop-loss-sweep` (mechanical intraday safety net) — that drive the bot end to end using the already-built core engine and universe module.

**Architecture:** One small, TDD'd Python change (`cmd_state` gains a `trading_mode` parameter, `cli.py` gains a `TRADING_MODE` constant) followed by two content-authoring tasks that write complete `SKILL.md` files under `.claude/skills/`. The skills are prompt/procedure text, not code — there's no automated test for them; verification is a manual end-to-end run once Robinhood's MCP is connected, per the design spec's testing strategy.

**Tech Stack:** Python 3.11+ (existing stack, no new dependencies), `pytest` for the one code task, Markdown/YAML frontmatter for the two skill files.

## Global Constraints

- No new third-party dependencies.
- No behavior change to any existing `cli.py` command other than `state` gaining the new field — `risk-check`, `record-fill`, `check-stop-losses`, and `universe` are untouched.
- Both `SKILL.md` files must instruct Claude to read `trading_mode` from `cli.py state`'s output as their first step, never to hardcode or assume the mode.
- Both skills must instruct: never fabricate a price when an MCP quote fails — skip that symbol for the cycle/sweep instead.
- The daily-cycle skill must never call the live `place_equity_order` MCP tool while `trading_mode` is `"paper"`.
- Every code task (Task 1, Task 2) ends green (`pytest` passing) before moving to the next.

---

### Task 1: `cmd_state` gains a `trading_mode` field

**Files:**
- Modify: `robinhood_bot/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `cmd_state(ledger_path: Path, starting_cash: float, prices: dict[str, float], today: date, trading_mode: str) -> dict` — same return shape as before, plus a `"trading_mode"` key holding the passed-through value verbatim.

- [ ] **Step 1: Update the three existing tests and add one new test**

Replace the full contents of `tests/test_commands.py`'s three `cmd_state` calls (lines 20-22, 38, 50) and add a fourth test. The file's `cmd_state`-related section becomes:

```python
def test_cmd_state_computes_total_equity_and_pnl(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        month="2026-07",
        month_start_equity=10_000.0,
    )
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper",
    )

    assert result["cash"] == 5_000.0
    assert result["active_positions"][0]["current_value"] == 1_100.0
    assert result["active_positions"][0]["unrealized_pnl_pct"] == 0.1
    assert result["total_equity"] == 6_100.0


def test_cmd_state_marks_missing_price_as_stale(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    )
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper",
    )

    assert result["active_positions"][0]["stale_price"] is True
    assert result["active_positions"][0]["current_value"] == 1_000.0
    assert result["active_positions"][0]["unrealized_pnl_pct"] is None


def test_cmd_state_rolls_month_and_persists(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(cash=10_000.0, month="2026-06", month_start_equity=9_000.0)
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 1), trading_mode="paper",
    )

    assert result["month"] == "2026-07"
    assert result["month_start_equity"] == 10_000.0

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.month == "2026-07"


def test_cmd_state_includes_trading_mode(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=1_000.0))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="live",
    )

    assert result["trading_mode"] == "live"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL — the three updated tests raise `TypeError: cmd_state() got an unexpected keyword argument 'trading_mode'`, and the new test fails the same way.

- [ ] **Step 3: Update `cmd_state`'s signature and return value**

In `robinhood_bot/commands.py`, replace:

```python
def cmd_state(ledger_path: Path, starting_cash: float, prices: dict[str, float], today: date) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    active_out = [_position_summary(p, prices) for p in state.active_positions]
    long_hold_out = [_position_summary(p, prices) for p in state.long_hold_positions]
    positions_value = sum(o["current_value"] for o in active_out + long_hold_out)
    total_equity = state.cash + positions_value

    roll_month_if_needed(state, today, total_equity)
    ledger.save_state(ledger_path, state)

    return {
        "cash": state.cash,
        "active_positions": active_out,
        "long_hold_positions": long_hold_out,
        "total_equity": total_equity,
        "month": state.month,
        "month_start_equity": state.month_start_equity,
        "monthly_return_pct": (
            (total_equity / state.month_start_equity - 1.0)
            if state.month_start_equity > 0
            else 0.0
        ),
    }
```

with:

```python
def cmd_state(
    ledger_path: Path,
    starting_cash: float,
    prices: dict[str, float],
    today: date,
    trading_mode: str,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    active_out = [_position_summary(p, prices) for p in state.active_positions]
    long_hold_out = [_position_summary(p, prices) for p in state.long_hold_positions]
    positions_value = sum(o["current_value"] for o in active_out + long_hold_out)
    total_equity = state.cash + positions_value

    roll_month_if_needed(state, today, total_equity)
    ledger.save_state(ledger_path, state)

    return {
        "trading_mode": trading_mode,
        "cash": state.cash,
        "active_positions": active_out,
        "long_hold_positions": long_hold_out,
        "total_equity": total_equity,
        "month": state.month,
        "month_start_equity": state.month_start_equity,
        "monthly_return_pct": (
            (total_equity / state.month_start_equity - 1.0)
            if state.month_start_equity > 0
            else 0.0
        ),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (17 tests — 16 existing + 1 new)

- [ ] **Step 5: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: add trading_mode field to cmd_state output"
```

---

### Task 2: `cli.py` gains a `TRADING_MODE` constant

**Files:**
- Modify: `robinhood_bot/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cmd_state(..., trading_mode: str)` (Task 1).
- Produces: module-level `TRADING_MODE = "paper"` constant in `cli.py`, threaded into the `state` subcommand's `cmd_state` call. Overridable via `monkeypatch.setattr(cli, "TRADING_MODE", ...)` in tests, matching the existing pattern for `LEDGER_PATH`/`STARTING_CASH`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_cli_state_command_includes_trading_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")
    monkeypatch.setattr(cli, "TRADING_MODE", "live")

    exit_code = cli.main(["state", "--prices-json", "{}"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_mode"] == "live"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `TypeError` from `cmd_state` missing the `trading_mode` argument (since `cli.py` doesn't pass it yet), surfaced as an unhandled exception during `cli.main(...)`.

- [ ] **Step 3: Add the constant and thread it through**

In `robinhood_bot/cli.py`, add `TRADING_MODE = "paper"` alongside the other module-level constants:

```python
LEDGER_PATH = Path("data/ledger.json")
TRADE_LOG_PATH = Path("data/trade_log.csv")
UNIVERSE_CACHE_PATH = Path("data/universe_cache.json")
STARTING_CASH = 10_000.0
TRADING_MODE = "paper"
```

Then update the `state` dispatch branch, replacing:

```python
    if args.command == "state":
        result = commands.cmd_state(LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today)
```

with:

```python
    if args.command == "state":
        result = commands.cmd_state(
            LEDGER_PATH, STARTING_CASH, _parse_prices(args.prices_json), today, TRADING_MODE
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (3 tests — 2 existing + 1 new)

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -v`
Expected: PASS (87 tests total — 85 from before this plan, +1 from Task 1, +1 from this task)

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: add TRADING_MODE constant to cli.py"
```

---

### Task 3: Daily trading cycle skill

**Files:**
- Create: `.claude/skills/robinhood-trading/SKILL.md`

**Interfaces:**
- Consumes: `cli.py state` (with `trading_mode` from Task 2), `cli.py universe`, `cli.py risk-check`, `cli.py record-fill`; Robinhood MCP quote and order-placement tools (names documented in the design spec, subject to verification once connected).
- Produces: nothing consumed by other tasks — this is a leaf procedure document.

- [ ] **Step 1: Write the complete file**

```markdown
---
name: robinhood-trading
description: Run the daily research-and-trade cycle for the Robinhood paper/live trading bot — fetches ranked candidates, researches a shortlist, gates every trade through cli.py risk-check, and records fills. Invoke once per trading day, after market close.
---

# Daily Trading Cycle

Run this once per trading day, after market close. It reads current
holdings and mode, gets a ranked candidate universe, researches a
shortlist, proposes trades, gates every trade through the hard risk
limits in `risk_engine.py`, and executes.

All `cli.py` commands below assume the project's virtualenv is active
(`python -m robinhood_bot.cli ...`). If it isn't, activate it first
(`.venv\Scripts\activate` on Windows).

## Step 1 — Read mode & current holdings

```
python -m robinhood_bot.cli state --prices-json "{}"
```

Prices come back marked `stale_price: true` here — that's expected and
fine, this call is only to learn:
- `trading_mode`: `"paper"` or `"live"`. **This governs everything below.**
  Never call the live order-placement MCP tool while this is `"paper"`.
- The symbols currently in `active_positions` and `long_hold_positions`.
- Current `month_start_equity` and `monthly_return_pct`, for context on
  progress toward this month's return goal.

## Step 2 — Get the ranked universe

```
python -m robinhood_bot.cli universe
```

This uses a weekly-cached membership list by default (fast). Only pass
`--refresh` if explicitly asked to force a refresh.

## Step 3 — Build today's research shortlist

From the `candidates` list (sorted by `combined_rank`, descending), take:
- The top 15 candidates whose `category` is `"sp500"` or `"nasdaq100"`.
- All candidates whose `category` is `"leveraged"` (there are only 3 —
  TQQQ, UPRO, SOXL — so this just means include all of them).
- Every symbol from Step 1's `active_positions` and `long_hold_positions`
  that isn't already in the shortlist, so open positions are always
  reconsidered even if they've fallen out of the top rankings.

## Step 4 — Get fresh quotes

Using the Robinhood MCP quote tool (e.g. `get_equity_quotes`), fetch a
current price for every symbol in the Step 3 shortlist.

**If a quote fails for any symbol: skip that symbol for this cycle.**
Never fabricate, estimate, or reuse a stale price in its place.

## Step 5 — Refresh state with real prices

```
python -m robinhood_bot.cli state --prices-json "<fresh quotes from Step 4, as a JSON object of symbol: price>"
```

Now `total_equity`, `unrealized_pnl_pct`, and `monthly_return_pct` are
accurate, not placeholder values.

## Step 6 — Research and decide, per shortlisted symbol

For each symbol currently **held** (active or long-hold):
- Note its lifecycle `status` (`ACTIVE`, `WAITING`, `LONG_HOLD`) and
  `unrealized_pnl_pct` from Step 5.
- `LONG_HOLD` positions are not part of today's short-term rotation —
  only consider selling one if it has clearly recovered and you'd
  exit it; otherwise leave it alone.
- For `ACTIVE`/`WAITING` positions, decide: propose **SELL** (if you'd
  exit today) or **HOLD** (do nothing).

For each shortlisted symbol **not currently held**:
- Consider its `combined_rank` (volatility), `realized_vol`/`atr_pct`,
  and recent price action from the fresh quote.
- Decide: propose **BUY** (a new position) or skip it.
- You can open at most as many new positions as there are free slots
  out of the 5-slot active cap (`5 - len(active_positions)` from
  Step 5's `active_positions`, since `WAITING` positions still occupy a
  slot).

## Step 7 — Gate every proposed BUY/SELL

For every proposed trade, before doing anything else:

```
python -m robinhood_bot.cli risk-check buy SYMBOL --value <proposed dollar amount> --prices-json "<fresh quotes>"
python -m robinhood_bot.cli risk-check sell SYMBOL --prices-json "<fresh quotes>"
```

- If `"approved": false`, **do not execute this trade.** Read `"reason"`
  and either propose a smaller size / different symbol, or fall back to
  HOLD. Never override a rejection.
- For an approved BUY, `"max_position_value"` is the ceiling. Compute a
  whole-share quantity: `floor(min(proposed_value, max_position_value) /
  fresh_quote_price)`. You may propose fewer shares than the ceiling
  allows.

## Step 8 — Execute approved trades

**If `trading_mode` is `"paper"`:**

```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote price> --reason "<why>"
```

Never call the live order-placement MCP tool in this mode.

**If `trading_mode` is `"live"`:**

1. Call the Robinhood MCP order-placement tool (e.g.
   `place_equity_order`) for the approved trade.
2. Once it confirms a fill, call `record-fill` using the **actual**
   filled quantity and price from that tool's response — never the
   pre-trade quote, even if they're close.

```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual fill price> --reason "<why>"
```

If order placement fails: do not call `record-fill`. Leave the ledger
untouched and note the failure in your summary — surface it, don't
retry silently or guess at what happened.

## Step 9 — Summarize

Run `python -m robinhood_bot.cli state --prices-json "<fresh quotes>"`
one more time and report to the user:
- What trades were made today and why (or why none were made).
- Current `monthly_return_pct` against the monthly goal.
- Anything that failed or was skipped (a quote that didn't come back, an
  order that failed to place), stated plainly.
```

- [ ] **Step 2: Validate the file's structure**

Run: `head -n 4 .claude/skills/robinhood-trading/SKILL.md`
Expected output:
```
---
name: robinhood-trading
description: Run the daily research-and-trade cycle for the Robinhood paper/live trading bot — fetches ranked candidates, researches a shortlist, gates every trade through cli.py risk-check, and records fills. Invoke once per trading day, after market close.
---
```

Run: `grep -c "^## Step" .claude/skills/robinhood-trading/SKILL.md`
Expected output: `9`

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md
git commit -m "docs: add daily trading cycle skill"
```

---

### Task 4: Stop-loss sweep skill

**Files:**
- Create: `.claude/skills/robinhood-stop-loss-sweep/SKILL.md`

**Interfaces:**
- Consumes: `cli.py state`, `cli.py check-stop-losses`, `cli.py record-fill`; Robinhood MCP quote and order-placement tools.
- Produces: nothing consumed by other tasks — leaf procedure document.

- [ ] **Step 1: Write the complete file**

```markdown
---
name: robinhood-stop-loss-sweep
description: Mechanical intraday safety-net sweep for the Robinhood trading bot — checks open positions against hard stop-loss/profit-target thresholds and exits any that breach them. No research, no discretion. Invoke at a fixed point mid-trading-day, between daily-cycle runs.
---

# Stop-Loss Sweep

Run this at a fixed point during the trading day (e.g. midday), between
daily-cycle runs. This is deliberately mechanical: no research, no
judgment calls. Its only job is to catch a position that's breached a
hard threshold before the next full daily cycle would notice.

All `cli.py` commands below assume the project's virtualenv is active
(`python -m robinhood_bot.cli ...`).

## Step 1 — Read current holdings

```
python -m robinhood_bot.cli state --prices-json "{}"
```

Note `trading_mode` and every symbol in `active_positions` (this
includes both `ACTIVE` and `WAITING` status positions — both occupy an
active slot and both are checked here).

## Step 2 — Get fresh quotes

Using the Robinhood MCP quote tool, fetch a current price for every
symbol from Step 1's `active_positions`.

**If a quote fails for any symbol: skip that symbol this sweep.** Never
fabricate or reuse a stale price.

## Step 3 — Run the stop-loss check

```
python -m robinhood_bot.cli check-stop-losses --prices-json "<fresh quotes>" --apply
```

This one command evaluates every active position against its
stop-loss/profit-target thresholds and the long-hold grace period, and
returns a `results` list. Because `--apply` was passed, any
`PROMOTE_LONG_HOLD` result has **already been applied** to the ledger —
the position has moved from `active_positions` to `long_hold_positions`.
You don't need to do anything further for those.

## Step 4 — Execute any SELL results

For each entry in `results` where `"action": "SELL"`:

**If `trading_mode` is `"paper"`:**

```
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote from Step 2> --reason "stop-loss sweep: profit target hit"
```

**If `trading_mode` is `"live"`:**

1. Call the Robinhood MCP order-placement tool (e.g.
   `place_equity_order`) to sell the position.
2. Call `record-fill` using the actual filled quantity/price from that
   tool's response, not the Step 2 quote.

Entries with `"action": "SKIP"` or `"action": "HOLD"` need no action.

## Step 5 — Report

One or two lines: what (if anything) was sold and why, and the current
cash/active-position count after this sweep. No further analysis —
that's the daily cycle's job, not this one.
```

- [ ] **Step 2: Validate the file's structure**

Run: `head -n 4 .claude/skills/robinhood-stop-loss-sweep/SKILL.md`
Expected output:
```
---
name: robinhood-stop-loss-sweep
description: Mechanical intraday safety-net sweep for the Robinhood trading bot — checks open positions against hard stop-loss/profit-target thresholds and exits any that breach them. No research, no discretion. Invoke at a fixed point mid-trading-day, between daily-cycle runs.
---
```

Run: `grep -c "^## Step" .claude/skills/robinhood-stop-loss-sweep/SKILL.md`
Expected output: `5`

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/robinhood-stop-loss-sweep/SKILL.md
git commit -m "docs: add stop-loss sweep skill"
```

---

## What This Plan Does Not Cover

- Connecting Claude Code to Robinhood's Agentic Trading MCP server — a
  prerequisite the user completes separately before either skill can
  actually run against live data.
- Verifying the documented MCP tool names (`get_equity_quotes`,
  `place_equity_order`, etc.) against the real, connected tool list —
  do this once connected, and correct either skill's text if names
  differ.
- Scheduled/automated invocation (Claude Code routines) — both skills
  are manually invoked for now, per the design spec.
- Numeric tuning of the shortlist size (15) or the sweep's exact
  time-of-day — reasonable defaults, not exhaustively tuned.
