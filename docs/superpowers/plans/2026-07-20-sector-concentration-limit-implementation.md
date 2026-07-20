# Sector Concentration Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the risk engine from opening more than `max_positions_per_sector` (default 1) active/waiting positions in the same GICS sector at once, so a single sector-wide selloff can't hit most of the portfolio at once.

**Architecture:** Sector is resolved once per symbol and carried as data — a new permanent `SectorCache` (`data/sector_cache.json`) resolved during `build_universe` for candidates, and a `Position.sector` field captured at buy time and persisted in the ledger for held positions. `evaluate_buy` gains a required `sector` parameter and a new rejection check right after the existing `is_held` check. Every layer above it (`commands.py`, `backtest_commands.py`, `cli.py`) threads `sector` through as an optional, defaulted parameter so only the two real production call sites (`cmd_risk_check`, `cmd_backtest_run`'s entries loop) must supply a real value.

**Tech Stack:** Python 3.11+, pytest, existing `robinhood_bot` package conventions (dataclasses, `Path`-based JSON persistence, `Protocol`-based `MarketDataClient`).

## Global Constraints

- `max_positions_per_sector` default is `1`, defined on `RiskConfig` — no runtime CLI flag to change it (spec: tunable in code only).
- The concentration check only scans `state.active_positions` (covers both `ACTIVE` and `WAITING` sub-statuses) — `LONG_HOLD` positions are explicitly excluded (spec non-goal).
- `sector=None` always bypasses the concentration check — this is the deliberate, permanent exemption path for leveraged funds (`TQQQ`/`UPRO`/`SOXL`), which never get a real GICS sector.
- A regular (non-leveraged) candidate whose sector can't be resolved is dropped entirely from `build_universe`'s output — never fabricated, mirroring the existing "no bars data" drop rule.
- `data/sector_cache.json` never expires once a symbol is cached (no TTL/staleness, unlike the weekly-refreshed `universe_cache.json`).
- `evaluate_buy`'s new `sector` parameter is **required** (no default) — only `cmd_risk_check` and `cmd_backtest_run` call it directly in production code, so both call sites must supply the value explicitly.
- Every other new `sector` parameter (`cmd_risk_check`, `cmd_record_fill`, `cmd_backtest_risk_check`, `cmd_backtest_record_fill`, and their CLI flags) is optional and defaults to `None`, to keep existing sell-path call sites and tests unaffected.
- Full test suite must stay green after every task: run `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v` before each commit.

---

### Task 1: Sector cache infrastructure in `universe.py`

**Files:**
- Modify: `robinhood_bot/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Produces: `SectorCache(sectors: dict[str, str])`; `load_sector_cache(path: Path) -> SectorCache | None`; `save_sector_cache(path: Path, cache: SectorCache) -> None`; `get_sector(client: MarketDataClient, cache_path: Path, symbol: str) -> str | None`; `MarketDataClient.fetch_sector(ticker: str) -> str | None` (new Protocol member).
- Consumes: existing `MarketDataClient` Protocol, existing `Path`-based JSON load/save pattern from `UniverseCache`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_universe.py`, directly after the `FakeMarketDataClient` class definition (before `test_refresh_membership_dedupes_overlap_preferring_sp500_category`). First, replace the `FakeMarketDataClient` class itself with this version (adds `sectors` and `fetch_sector`):

```python
class FakeMarketDataClient:
    def __init__(self, sp500=None, nasdaq100=None, market_caps=None, bars=None, sectors=None, raise_on_fetch=False):
        self.sp500 = sp500 or []
        self.nasdaq100 = nasdaq100 or []
        self.market_caps = market_caps or {}
        self.bars = bars or {}
        self.sectors = sectors or {}
        self.raise_on_fetch = raise_on_fetch
        self.calls = []

    def fetch_sp500_tickers(self):
        self.calls.append("sp500")
        if self.raise_on_fetch:
            raise RuntimeError("network error")
        return self.sp500

    def fetch_nasdaq100_tickers(self):
        self.calls.append("nasdaq100")
        if self.raise_on_fetch:
            raise RuntimeError("network error")
        return self.nasdaq100

    def fetch_market_caps(self, tickers):
        self.calls.append("market_caps")
        return {t: self.market_caps[t] for t in tickers if t in self.market_caps}

    def fetch_daily_bars(self, ticker, lookback_days):
        self.calls.append(f"bars:{ticker}")
        return self.bars.get(ticker, [])

    def fetch_sector(self, ticker):
        self.calls.append(f"sector:{ticker}")
        return self.sectors.get(ticker)
```

Then append these new tests immediately after it (still before `test_refresh_membership_dedupes_overlap_preferring_sp500_category`):

```python
from robinhood_bot.universe import SectorCache, load_sector_cache, save_sector_cache, get_sector


def test_load_sector_cache_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "sector_cache.json"
    assert load_sector_cache(path) is None


def test_save_and_load_sector_cache_round_trip(tmp_path):
    path = tmp_path / "sector_cache.json"
    save_sector_cache(path, SectorCache(sectors={"AAPL": "Technology"}))
    loaded = load_sector_cache(path)
    assert loaded.sectors == {"AAPL": "Technology"}


def test_get_sector_returns_cached_value_without_fetching(tmp_path):
    path = tmp_path / "sector_cache.json"
    save_sector_cache(path, SectorCache(sectors={"AAPL": "Technology"}))
    client = FakeMarketDataClient()

    sector = get_sector(client, path, "AAPL")

    assert sector == "Technology"
    assert "sector:AAPL" not in client.calls


def test_get_sector_fetches_and_caches_on_cache_miss(tmp_path):
    path = tmp_path / "sector_cache.json"
    client = FakeMarketDataClient(sectors={"MSFT": "Technology"})

    sector = get_sector(client, path, "MSFT")

    assert sector == "Technology"
    reloaded = load_sector_cache(path)
    assert reloaded.sectors == {"MSFT": "Technology"}


def test_get_sector_returns_none_and_does_not_cache_on_fetch_failure(tmp_path):
    path = tmp_path / "sector_cache.json"
    client = FakeMarketDataClient(sectors={})

    sector = get_sector(client, path, "UNKNOWN")

    assert sector is None
    assert load_sector_cache(path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: FAIL — `ImportError: cannot import name 'SectorCache'` (and `load_sector_cache`, `save_sector_cache`, `get_sector` don't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/universe.py`, add `fetch_sector` to the `MarketDataClient` Protocol — change:

```python
class MarketDataClient(Protocol):
    def fetch_sp500_tickers(self) -> list[str]: ...
    def fetch_nasdaq100_tickers(self) -> list[str]: ...
    def fetch_market_caps(self, tickers: list[str]) -> dict[str, float]: ...
    def fetch_daily_bars(self, ticker: str, lookback_days: int) -> list[Bar]: ...
```

to:

```python
class MarketDataClient(Protocol):
    def fetch_sp500_tickers(self) -> list[str]: ...
    def fetch_nasdaq100_tickers(self) -> list[str]: ...
    def fetch_market_caps(self, tickers: list[str]) -> dict[str, float]: ...
    def fetch_daily_bars(self, ticker: str, lookback_days: int) -> list[Bar]: ...
    def fetch_sector(self, ticker: str) -> str | None: ...
```

Then, directly after the `UniverseCache` dataclass and before the `Candidate` dataclass, add (grouping it with the other cache dataclasses):

```python
@dataclass
class SectorCache:
    sectors: dict[str, str]
```

Then, directly after `save_cache` (the existing `UniverseCache` save function) and before `is_cache_stale`, add:

```python
def sector_cache_to_dict(cache: SectorCache) -> dict:
    return {"sectors": cache.sectors}


def sector_cache_from_dict(data: dict) -> SectorCache:
    return SectorCache(sectors=data["sectors"])


def load_sector_cache(path: Path) -> SectorCache | None:
    if not path.exists():
        return None
    with path.open("r") as f:
        return sector_cache_from_dict(json.load(f))


def save_sector_cache(path: Path, cache: SectorCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(sector_cache_to_dict(cache), f, indent=2)


def get_sector(client: MarketDataClient, cache_path: Path, symbol: str) -> str | None:
    cache = load_sector_cache(cache_path) or SectorCache(sectors={})
    if symbol in cache.sectors:
        return cache.sectors[symbol]

    sector = client.fetch_sector(symbol)
    if sector is None:
        return None

    cache.sectors[symbol] = sector
    save_sector_cache(cache_path, cache)
    return sector
```

Note: `get_sector` references `MarketDataClient` in its type hint, so it must be defined after the Protocol — placing it after `save_cache` but the Protocol is already defined above `save_cache` in the current file, so ordering is fine as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: PASS (all existing + 4 new sector-cache tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (no regressions — `FakeMarketDataClient`'s new `sectors`/`fetch_sector` are purely additive with a default).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/universe.py tests/test_universe.py
git commit -m "feat: add permanent sector cache and get_sector lookup"
```

---

### Task 2: `Candidate.sector` + `build_universe` sector resolution

**Files:**
- Modify: `robinhood_bot/universe.py`
- Modify: `robinhood_bot/universe_client.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `SectorCache`, `get_sector` (Task 1); `MarketDataClient.fetch_sector` (Task 1, Protocol).
- Produces: `Candidate.sector: str | None = None`; `build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False) -> list[Candidate]` (signature changed — `sector_cache_path` is a new required positional parameter inserted after `cache_path`); `LiveMarketDataClient.fetch_sector(ticker: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_universe.py`, change the `Candidate` dataclass usage is unaffected (it's defaulted), but the four existing `build_universe` tests and the leveraged-funds test all call `build_universe` with the old 4-positional-arg signature and don't supply sectors, so they'll break once Task 2's implementation lands. Update them now (in this same step, alongside the new tests) so Step 2 below shows the real failure (missing `fetch_sector`/signature mismatch), not a spurious one.

Replace `test_build_universe_ranks_by_realized_vol_when_mode_is_realized_vol` with:

```python
def test_build_universe_ranks_by_realized_vol_when_mode_is_realized_vol(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    bars_low = [Bar(101.0, 99.0, 100.0), Bar(101.0, 99.5, 100.2), Bar(100.8, 99.6, 100.1)]
    bars_high = [Bar(110.0, 90.0, 100.0), Bar(115.0, 85.0, 105.0), Bar(120.0, 80.0, 95.0)]
    client = FakeMarketDataClient(
        sp500=["LOW", "HIGH"], nasdaq100=[],
        market_caps={"LOW": 100.0, "HIGH": 200.0},
        bars={"LOW": bars_low, "HIGH": bars_high},
        sectors={"LOW": "Technology", "HIGH": "Technology"},
    )
    cfg = UniverseConfig(
        top_n_sp500=2, top_n_nasdaq100=2, leveraged_funds=[],
        realized_vol_window_days=2, atr_window_days=2, ranking_mode="realized_vol",
    )

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["HIGH", "LOW"]
    assert candidates[0].combined_rank == 1.0
    assert candidates[1].combined_rank == 0.0
```

Replace `test_build_universe_drops_symbols_with_no_bars` with:

```python
def test_build_universe_drops_symbols_with_no_bars(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    client = FakeMarketDataClient(
        sp500=["A", "B"], nasdaq100=[],
        market_caps={"A": 100.0, "B": 200.0},
        bars={"A": [Bar(101.0, 99.0, 100.0), Bar(102.0, 99.0, 101.0)]},
        sectors={"A": "Technology", "B": "Technology"},
    )
    cfg = UniverseConfig(top_n_sp500=2, top_n_nasdaq100=2, leveraged_funds=[])

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["A"]
```

Replace `test_build_universe_includes_leveraged_funds` with:

```python
def test_build_universe_includes_leveraged_funds(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    bars = [Bar(101.0, 99.0, 100.0), Bar(102.0, 99.0, 101.0)]
    client = FakeMarketDataClient(sp500=[], nasdaq100=[], market_caps={}, bars={"TQQQ": bars})
    cfg = UniverseConfig(top_n_sp500=0, top_n_nasdaq100=0, leveraged_funds=["TQQQ"])

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["TQQQ"]
    assert candidates[0].category == "leveraged"
    assert candidates[0].sector is None
```

Replace `test_build_universe_both_mode_averages_percentile_ranks` with:

```python
def test_build_universe_both_mode_averages_percentile_ranks(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    bars_a = [Bar(101.0, 99.0, 100.0), Bar(101.0, 99.5, 100.2), Bar(100.8, 99.6, 100.1)]
    bars_b = [Bar(110.0, 90.0, 100.0), Bar(115.0, 85.0, 105.0), Bar(120.0, 80.0, 95.0)]
    client = FakeMarketDataClient(
        sp500=["A", "B"], nasdaq100=[],
        market_caps={"A": 100.0, "B": 200.0},
        bars={"A": bars_a, "B": bars_b},
        sectors={"A": "Technology", "B": "Financials"},
    )
    cfg = UniverseConfig(
        top_n_sp500=2, top_n_nasdaq100=2, leveraged_funds=[],
        realized_vol_window_days=2, atr_window_days=2, ranking_mode="both",
    )

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["B", "A"]
    assert candidates[0].combined_rank == 1.0
    assert candidates[1].combined_rank == 0.0
```

Add two new tests directly after `test_build_universe_both_mode_averages_percentile_ranks`:

```python
def test_build_universe_drops_candidate_with_unresolvable_sector(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    bars = [Bar(101.0, 99.0, 100.0), Bar(102.0, 99.0, 101.0)]
    client = FakeMarketDataClient(
        sp500=["A", "B"], nasdaq100=[],
        market_caps={"A": 100.0, "B": 200.0},
        bars={"A": bars, "B": bars},
        sectors={"A": "Technology"},
    )
    cfg = UniverseConfig(top_n_sp500=2, top_n_nasdaq100=2, leveraged_funds=[])

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert [c.symbol for c in candidates] == ["A"]


def test_build_universe_includes_resolved_sector_on_candidate(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    sector_cache_path = tmp_path / "sector_cache.json"
    today = date(2026, 7, 19)
    bars = [Bar(101.0, 99.0, 100.0), Bar(102.0, 99.0, 101.0)]
    client = FakeMarketDataClient(
        sp500=["A"], nasdaq100=[],
        market_caps={"A": 100.0},
        bars={"A": bars},
        sectors={"A": "Healthcare"},
    )
    cfg = UniverseConfig(top_n_sp500=1, top_n_nasdaq100=1, leveraged_funds=[])

    candidates = build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh=False)

    assert candidates[0].sector == "Healthcare"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: FAIL — `TypeError: build_universe() takes from 4 to 5 positional arguments but 6 were given` (or similar) on every updated/new `build_universe` test.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/universe.py`, change the `Candidate` dataclass from:

```python
@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    realized_vol: float
    atr_pct: float
    combined_rank: float
```

to:

```python
@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    realized_vol: float
    atr_pct: float
    combined_rank: float
    sector: str | None = None
```

Then replace `build_universe` entirely:

```python
def build_universe(
    client: MarketDataClient,
    cache_path: Path,
    sector_cache_path: Path,
    cfg: UniverseConfig,
    today: date,
    force_refresh: bool = False,
) -> list[Candidate]:
    members = get_membership(client, cache_path, cfg, today, force_refresh)

    sectors: dict[str, str | None] = {}
    resolved_members = []
    for member in members:
        sector = get_sector(client, sector_cache_path, member.symbol)
        if sector is None:
            continue
        sectors[member.symbol] = sector
        resolved_members.append(member)

    leveraged = [CachedMember(symbol, "leveraged", 0.0) for symbol in cfg.leveraged_funds]
    for member in leveraged:
        sectors[member.symbol] = None
    all_members = resolved_members + leveraged

    lookback = max(cfg.realized_vol_window_days, cfg.atr_window_days) + 1
    realized_vols: dict[str, float] = {}
    atr_pcts: dict[str, float] = {}

    for member in all_members:
        bars = client.fetch_daily_bars(member.symbol, lookback)
        if not bars:
            continue
        closes = [bar.close for bar in bars]
        realized_vols[member.symbol] = realized_volatility(closes[-(cfg.realized_vol_window_days + 1):])
        atr_pcts[member.symbol] = average_true_range_pct(bars[-(cfg.atr_window_days + 1):])

    vol_ranks = percentile_ranks(realized_vols)
    atr_ranks = percentile_ranks(atr_pcts)

    candidates = []
    for member in all_members:
        if member.symbol not in realized_vols:
            continue
        if cfg.ranking_mode == "realized_vol":
            score = vol_ranks[member.symbol]
        elif cfg.ranking_mode == "atr_pct":
            score = atr_ranks[member.symbol]
        else:
            score = (vol_ranks[member.symbol] + atr_ranks[member.symbol]) / 2
        candidates.append(Candidate(
            symbol=member.symbol,
            category=member.category,
            market_cap=member.market_cap,
            realized_vol=realized_vols[member.symbol],
            atr_pct=atr_pcts[member.symbol],
            combined_rank=score,
            sector=sectors[member.symbol],
        ))

    candidates.sort(key=lambda c: c.combined_rank, reverse=True)
    return candidates
```

In `robinhood_bot/universe_client.py`, add a `fetch_sector` method to `LiveMarketDataClient`, directly after `fetch_market_caps`:

```python
    def fetch_sector(self, ticker: str) -> str | None:
        try:
            # NOTE: sector isn't available on fast_info -- only the full
            # (slower) .info property exposes GICS sector classification.
            info = yf.Ticker(ticker).info
            sector = info.get("sector")
        except Exception:
            return None
        return sector if sector else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_universe.py -v`
Expected: PASS (all existing + 2 new tests; leveraged-funds test's new `sector is None` assertion passes)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: FAIL only in `tests/test_cli.py` — `test_cli_universe_command_prints_json` and `test_cli_backtest_run_command_delegates_to_backtest_commands` monkeypatch `build_universe` with the old signature. This is expected; `cli.py` and `test_cli.py` are updated in Task 7. Confirm no *other* file fails.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/universe.py robinhood_bot/universe_client.py tests/test_universe.py
git commit -m "feat: resolve and attach GICS sector to universe candidates"
```

---

### Task 3: `Position.sector` field + ledger persistence

**Files:**
- Modify: `robinhood_bot/portfolio_state.py`
- Modify: `robinhood_bot/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces: `Position.sector: str | None = None`.
- Consumes: existing `_position_to_dict`/`_position_from_dict` backward-compatible `.get()` pattern.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py`. First add `import json` to the top of the file (it currently only imports `date`, `ledger`, `Position`/`PositionStatus`/`PortfolioState`):

```python
import json
from datetime import date

from robinhood_bot import ledger
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState
```

Then append at the end of the file:

```python
def test_save_and_load_round_trip_preserves_sector(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(
        cash=8_000.0,
        active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
        ],
    )
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].sector == "Technology"


def test_load_state_defaults_missing_sector_to_none_for_old_ledger_files(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "cash": 5_000.0,
        "active_positions": [{
            "symbol": "AAPL", "qty": 10, "entry_price": 100.0,
            "entry_date": "2026-07-01", "status": "ACTIVE", "underwater_since": None,
        }],
        "long_hold_positions": [],
        "month": "", "month_start_equity": 0.0, "week": "", "week_realized_pnl": 0.0,
    }))

    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].sector is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_ledger.py -v`
Expected: FAIL — `TypeError: Position.__init__() got an unexpected keyword argument 'sector'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/portfolio_state.py`, change the `Position` dataclass from:

```python
@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: date
    status: PositionStatus
    underwater_since: date | None = None
```

to:

```python
@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: date
    status: PositionStatus
    underwater_since: date | None = None
    sector: str | None = None
```

In `robinhood_bot/ledger.py`, change `_position_to_dict` from:

```python
def _position_to_dict(position: Position) -> dict:
    return {
        "symbol": position.symbol,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "status": position.status.value,
        "underwater_since": (
            position.underwater_since.isoformat() if position.underwater_since else None
        ),
    }
```

to:

```python
def _position_to_dict(position: Position) -> dict:
    return {
        "symbol": position.symbol,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "status": position.status.value,
        "underwater_since": (
            position.underwater_since.isoformat() if position.underwater_since else None
        ),
        "sector": position.sector,
    }
```

And change `_position_from_dict` from:

```python
def _position_from_dict(data: dict) -> Position:
    return Position(
        symbol=data["symbol"],
        qty=data["qty"],
        entry_price=data["entry_price"],
        entry_date=date.fromisoformat(data["entry_date"]),
        status=PositionStatus(data["status"]),
        underwater_since=(
            date.fromisoformat(data["underwater_since"]) if data["underwater_since"] else None
        ),
    )
```

to:

```python
def _position_from_dict(data: dict) -> Position:
    return Position(
        symbol=data["symbol"],
        qty=data["qty"],
        entry_price=data["entry_price"],
        entry_date=date.fromisoformat(data["entry_date"]),
        status=PositionStatus(data["status"]),
        underwater_since=(
            date.fromisoformat(data["underwater_since"]) if data["underwater_since"] else None
        ),
        sector=data.get("sector"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_ledger.py tests/test_portfolio_state.py -v`
Expected: PASS (all existing + 2 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS except the two already-known `test_cli.py` failures from Task 2 (unrelated, fixed in Task 7).

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/portfolio_state.py robinhood_bot/ledger.py tests/test_ledger.py
git commit -m "feat: persist a position's sector in the ledger"
```

---

### Task 4: `RiskConfig.max_positions_per_sector` + `evaluate_buy` concentration check

**Files:**
- Modify: `robinhood_bot/risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `Position.sector` (Task 3).
- Produces: `RiskConfig.max_positions_per_sector: int = 1`; `evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector: str | None) -> BuyDecision` (signature changed — `sector` is a new **required** parameter, no default).

- [ ] **Step 1: Write the failing tests**

In `tests/test_risk_engine.py`, every existing call to `evaluate_buy` must add `sector=None` (this makes the tests fail for the right reason first — a missing-argument `TypeError` — then pass once the implementation adds the parameter). Update these six lines:

```python
    decision = evaluate_buy(state, "AAPL", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None)
```
(was: `test_evaluate_buy_rejects_when_symbol_already_held`, previously ended `cfg=cfg)`)

```python
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=8_000.0, cfg=cfg, sector=None)
```
(was: `test_evaluate_buy_rejects_when_circuit_breaker_tripped`)

```python
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None)
```
(was: `test_evaluate_buy_rejects_when_no_active_slots`)

```python
    decision = evaluate_buy(state, "MSFT", proposed_value=5_000.0, total_equity=10_000.0, cfg=cfg, sector=None)
```
(was: `test_evaluate_buy_rejects_when_oversized`)

```python
    decision = evaluate_buy(state, "MSFT", proposed_value=2_000.0, total_equity=10_000.0, cfg=cfg, sector=None)
```
(was: `test_evaluate_buy_rejects_when_insufficient_cash`)

```python
    decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None)
```
(was: `test_evaluate_buy_approves_happy_path`)

Then append these three new tests directly after `test_evaluate_buy_approves_happy_path`:

```python
def test_evaluate_buy_rejects_when_sector_concentration_limit_reached():
    cfg = RiskConfig(max_positions_per_sector=1)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
    ])
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector="Technology")
    assert decision.approved is False
    assert "sector concentration" in decision.reason


def test_evaluate_buy_approves_when_different_sector_held():
    cfg = RiskConfig(max_positions_per_sector=1, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
    ])
    decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials")
    assert decision.approved is True


def test_evaluate_buy_approves_when_sector_none_bypasses_concentration_check():
    cfg = RiskConfig(max_positions_per_sector=1, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0, active_positions=[
        Position("TQQQ", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector=None)
    ])
    decision = evaluate_buy(state, "UPRO", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None)
    assert decision.approved is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: FAIL — `TypeError: evaluate_buy() got an unexpected keyword argument 'sector'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/risk_engine.py`, change `RiskConfig` from:

```python
@dataclass
class RiskConfig:
    max_active_positions: int = 5
    stop_loss_pct: float = 0.05
    weekly_profit_goal: float = 500.0
    grace_period_days: int = 5
    max_position_pct: float = 0.20
    min_position_pct: float = 0.05
    long_hold_capital_cap_pct: float = 0.30
    monthly_circuit_breaker_pct: float = 0.10
```

to:

```python
@dataclass
class RiskConfig:
    max_active_positions: int = 5
    max_positions_per_sector: int = 1
    stop_loss_pct: float = 0.05
    weekly_profit_goal: float = 500.0
    grace_period_days: int = 5
    max_position_pct: float = 0.20
    min_position_pct: float = 0.05
    long_hold_capital_cap_pct: float = 0.30
    monthly_circuit_breaker_pct: float = 0.10
```

Then change `evaluate_buy` from:

```python
def evaluate_buy(
    state: PortfolioState,
    symbol: str,
    proposed_value: float,
    total_equity: float,
    cfg: RiskConfig,
) -> BuyDecision:
    max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)

    if state.is_held(symbol):
        return BuyDecision(False, "symbol already held", max_value)

    if circuit_breaker_tripped(state.month_start_equity, total_equity, cfg):
        return BuyDecision(False, "monthly circuit breaker tripped", max_value)

    if state.active_slot_count() >= cfg.max_active_positions:
        return BuyDecision(False, "no active slots available", max_value)

    if proposed_value > max_value:
        return BuyDecision(
            False, f"proposed value exceeds max position size of {max_value:.2f}", max_value
        )

    if proposed_value > state.cash:
        return BuyDecision(False, "insufficient cash", max_value)

    return BuyDecision(True, "approved", max_value)
```

to:

```python
def evaluate_buy(
    state: PortfolioState,
    symbol: str,
    proposed_value: float,
    total_equity: float,
    cfg: RiskConfig,
    sector: str | None,
) -> BuyDecision:
    max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)

    if state.is_held(symbol):
        return BuyDecision(False, "symbol already held", max_value)

    if sector is not None:
        sector_count = sum(1 for p in state.active_positions if p.sector == sector)
        if sector_count >= cfg.max_positions_per_sector:
            return BuyDecision(
                False,
                f"sector concentration: already at the {cfg.max_positions_per_sector}-position limit for {sector}",
                max_value,
            )

    if circuit_breaker_tripped(state.month_start_equity, total_equity, cfg):
        return BuyDecision(False, "monthly circuit breaker tripped", max_value)

    if state.active_slot_count() >= cfg.max_active_positions:
        return BuyDecision(False, "no active slots available", max_value)

    if proposed_value > max_value:
        return BuyDecision(
            False, f"proposed value exceeds max position size of {max_value:.2f}", max_value
        )

    if proposed_value > state.cash:
        return BuyDecision(False, "insufficient cash", max_value)

    return BuyDecision(True, "approved", max_value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_risk_engine.py -v`
Expected: PASS (all existing + 3 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: FAIL in `robinhood_bot/commands.py` and `robinhood_bot/backtest_commands.py` call sites (`evaluate_buy(state, symbol, proposed_value, total_equity, cfg)` is now missing the required `sector` argument) — expected, fixed in Tasks 5 and 6. Confirm `test_risk_engine.py`, `test_universe.py`, `test_ledger.py`, `test_portfolio_state.py` are all green.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/risk_engine.py tests/test_risk_engine.py
git commit -m "feat: reject a buy that would exceed the per-sector position limit"
```

---

### Task 5: `commands.py` sector plumbing

**Files:**
- Modify: `robinhood_bot/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `evaluate_buy(..., sector)` (Task 4, now required); `Position.sector` (Task 3).
- Produces: `cmd_risk_check(..., sector: str | None = None) -> dict`; `cmd_record_fill(..., sector: str | None = None) -> dict` (both optional, defaulted — keeps every existing call site working unchanged).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`, directly after `test_cmd_risk_check_buy_approves_happy_path`:

```python
def test_cmd_risk_check_buy_rejects_on_sector_concentration(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=10_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")],
        month_start_equity=10_000.0,
    ))
    cfg = RiskConfig(max_position_pct=0.20)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={"AAPL": 100.0}, cfg=cfg, sector="Technology",
    )

    assert result["approved"] is False
    assert "sector concentration" in result["reason"]
```

Append directly after `test_cmd_record_fill_buy_updates_cash_and_adds_position`:

```python
def test_cmd_record_fill_buy_persists_sector(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle", sector="Technology",
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].sector == "Technology"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: FAIL — `TypeError: cmd_risk_check() got an unexpected keyword argument 'sector'` and the same for `cmd_record_fill`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/commands.py`, change `cmd_risk_check` from:

```python
def cmd_risk_check(
    ledger_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    proposed_value: float,
    prices: dict[str, float],
    cfg: RiskConfig,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    positions_value = sum(
        prices.get(p.symbol, p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    total_equity = state.cash + positions_value

    if action == "buy":
        decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg)
        return {
            "approved": decision.approved,
            "reason": decision.reason,
            "max_position_value": decision.max_position_value,
        }
    if action == "sell":
        decision = evaluate_sell(state, symbol)
        return {"approved": decision.approved, "reason": decision.reason}

    raise ValueError(f"unknown action: {action}")
```

to:

```python
def cmd_risk_check(
    ledger_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    proposed_value: float,
    prices: dict[str, float],
    cfg: RiskConfig,
    sector: str | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)

    positions_value = sum(
        prices.get(p.symbol, p.entry_price) * p.qty
        for p in state.active_positions + state.long_hold_positions
    )
    total_equity = state.cash + positions_value

    if action == "buy":
        decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector)
        return {
            "approved": decision.approved,
            "reason": decision.reason,
            "max_position_value": decision.max_position_value,
        }
    if action == "sell":
        decision = evaluate_sell(state, symbol)
        return {"approved": decision.approved, "reason": decision.reason}

    raise ValueError(f"unknown action: {action}")
```

Then change `cmd_record_fill` from:

```python
def cmd_record_fill(
    ledger_path: Path,
    trade_log_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    qty: float,
    price: float,
    today: date,
    reason: str,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    roll_week_if_needed(state, today)

    if action == "buy":
        if state.is_held(symbol):
            raise ValueError(f"{symbol} already held")
        cost = qty * price
        if cost > state.cash:
            raise ValueError("insufficient cash for fill")
        state.cash -= cost
        state.active_positions.append(
            Position(
                symbol=symbol,
                qty=qty,
                entry_price=price,
                entry_date=today,
                status=PositionStatus.ACTIVE,
            )
        )
```

to:

```python
def cmd_record_fill(
    ledger_path: Path,
    trade_log_path: Path,
    starting_cash: float,
    action: str,
    symbol: str,
    qty: float,
    price: float,
    today: date,
    reason: str,
    sector: str | None = None,
) -> dict:
    state = ledger.load_state(ledger_path, starting_cash)
    roll_week_if_needed(state, today)

    if action == "buy":
        if state.is_held(symbol):
            raise ValueError(f"{symbol} already held")
        cost = qty * price
        if cost > state.cash:
            raise ValueError("insufficient cash for fill")
        state.cash -= cost
        state.active_positions.append(
            Position(
                symbol=symbol,
                qty=qty,
                entry_price=price,
                entry_date=today,
                status=PositionStatus.ACTIVE,
                sector=sector,
            )
        )
```

(The rest of `cmd_record_fill` — the `elif action == "sell":` branch and everything after — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_commands.py -v`
Expected: PASS (all existing + 2 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: FAIL only in `robinhood_bot/backtest_commands.py`'s `evaluate_buy` call inside `cmd_backtest_run` (still missing `sector` — fixed in Task 6). Confirm `test_commands.py` and everything from Tasks 1-4 stay green.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/commands.py tests/test_commands.py
git commit -m "feat: thread an optional sector through cmd_risk_check and cmd_record_fill"
```

---

### Task 6: `backtest_commands.py` sector wiring

**Files:**
- Modify: `robinhood_bot/backtest_commands.py`
- Test: `tests/test_backtest_commands.py`

**Interfaces:**
- Consumes: `commands.cmd_risk_check(..., sector)`, `commands.cmd_record_fill(..., sector)` (Task 5); `evaluate_buy(..., sector)` (Task 4).
- Produces: `cmd_backtest_risk_check(..., sector: str | None = None) -> dict`; `cmd_backtest_record_fill(..., sector: str | None = None) -> dict`; `cmd_backtest_run(..., candidate_sectors: dict[str, str], ...) -> dict` (signature changed — `candidate_sectors` is a new **required** parameter inserted directly after `candidate_symbols`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_backtest_commands.py`, the four existing `cmd_backtest_run` calls need `candidate_sectors={}` added (an empty dict is fine — none of these scenarios need sector enforcement, so `.get(symbol)` returning `None` preserves their existing behavior exactly). Update each:

`test_cmd_backtest_run_executes_deterministic_entry_exit_cycle` — change:
```python
    result = backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 5),
        candidate_symbols=["A"], store=store, cfg=cfg,
    )
```
to:
```python
    result = backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 5),
        candidate_symbols=["A"], candidate_sectors={}, store=store, cfg=cfg,
    )
```

`test_cmd_backtest_run_escalates_tier_across_days_in_same_week` — change:
```python
    backtest_commands.cmd_backtest_run(
        "run_escalation", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 6),
        candidate_symbols=["A"], store=store, cfg=cfg,
    )
```
to:
```python
    backtest_commands.cmd_backtest_run(
        "run_escalation", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 6),
        candidate_symbols=["A"], candidate_sectors={}, store=store, cfg=cfg,
    )
```

`test_cmd_backtest_run_promotes_expired_underwater_position_to_long_hold` — change:
```python
    backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=9_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=["B"], store=store, cfg=cfg, vol_window_days=2, atr_window_days=2,
    )
```
to:
```python
    backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=9_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=["B"], candidate_sectors={}, store=store, cfg=cfg, vol_window_days=2, atr_window_days=2,
    )
```

`test_cmd_backtest_run_sweeps_recovered_long_hold_position_for_profit` — change:
```python
    backtest_commands.cmd_backtest_run(
        "run2", tmp_path, starting_cash=1_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=[], store=store, cfg=cfg,
    )
```
to:
```python
    backtest_commands.cmd_backtest_run(
        "run2", tmp_path, starting_cash=1_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=[], candidate_sectors={}, store=store, cfg=cfg,
    )
```

Then append this new integration test directly after `test_cmd_backtest_run_sweeps_recovered_long_hold_position_for_profit` (before `test_cmd_backtest_report_computes_return_and_benchmark`):

```python
def test_cmd_backtest_run_skips_same_sector_candidate_for_next_ranked(tmp_path):
    # MSFT is already held (sector "Technology"), one free active slot. Of the
    # two candidates, AAPL2 ranks first (far higher volatility/ATR from its
    # wild swings below) but shares MSFT's sector, so it must be REJECTED by
    # the sector-concentration check; JPM ranks second but has a different
    # sector, so it's the one that should actually get bought. If the sector
    # check weren't wired into this loop, AAPL2 (the top-ranked candidate)
    # would be bought instead.
    bars = {
        "SPY": [HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0)],
        "AAPL2": [
            HistoricalBar(date(2025, 12, 30), 90.0, 110.0, 80.0, 100.0),
            HistoricalBar(date(2025, 12, 31), 100.0, 130.0, 70.0, 90.0),
            HistoricalBar(date(2026, 1, 2), 90.0, 120.0, 60.0, 110.0),
        ],
        "JPM": [
            HistoricalBar(date(2025, 12, 30), 149.0, 150.5, 148.5, 150.0),
            HistoricalBar(date(2025, 12, 31), 150.0, 151.0, 149.5, 150.2),
            HistoricalBar(date(2026, 1, 2), 150.2, 151.2, 149.8, 150.4),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            # MSFT (the already-held position) is intentionally NOT a key in
            # `bars` -- it isn't a candidate this run needs price history
            # for beyond the exits/equity phases, which already tolerate a
            # missing quote by falling back to entry_price. Use `.get` (not
            # `bars[symbol]`) so that fallback path doesn't KeyError instead.
            return [b for b in bars.get(symbol, []) if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=2, stop_loss_pct=0.5, weekly_profit_goal=100_000.0,
        max_position_pct=1.0, min_position_pct=1.0, grace_period_days=5,
        max_positions_per_sector=1,
    )

    paths = backtest_commands.resolve_run_paths("run_sector", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=10_000.0,
        active_positions=[Position("MSFT", 5, 300.0, date(2025, 12, 1), PositionStatus.ACTIVE, sector="Technology")],
        month="2026-01",
        month_start_equity=11_500.0,
    ))

    backtest_commands.cmd_backtest_run(
        "run_sector", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={"AAPL2": "Technology", "JPM": "Financials"},
        store=store, cfg=cfg, vol_window_days=2, atr_window_days=2,
    )

    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    symbols = {p.symbol for p in final_state.active_positions}
    assert symbols == {"MSFT", "JPM"}
    jpm = next(p for p in final_state.active_positions if p.symbol == "JPM")
    assert jpm.sector == "Financials"
    assert jpm.qty == 66

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert [r["symbol"] for r in rows] == ["JPM"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: FAIL — `TypeError: cmd_backtest_run() got an unexpected keyword argument 'candidate_sectors'`.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/backtest_commands.py`, change `cmd_backtest_risk_check` from:

```python
def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg)
```

to:

```python
def cmd_backtest_risk_check(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    proposed_value: float, prices: dict[str, float], cfg: RiskConfig, sector: str | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_risk_check(paths.ledger, starting_cash, action, symbol, proposed_value, prices, cfg, sector)
```

Change `cmd_backtest_record_fill` from:

```python
def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason,
    )
```

to:

```python
def cmd_backtest_record_fill(
    run_id: str, base_dir: Path, starting_cash: float, action: str, symbol: str,
    qty: float, price: float, asof: date, reason: str, sector: str | None = None,
) -> dict:
    paths = resolve_run_paths(run_id, base_dir)
    return commands.cmd_record_fill(
        paths.ledger, paths.trade_log, starting_cash, action, symbol, qty, price, asof, reason, sector,
    )
```

Change `cmd_backtest_run`'s signature and entries-loop block. The signature changes from:

```python
def cmd_backtest_run(
    run_id: str,
    base_dir: Path,
    starting_cash: float,
    start: date,
    end: date,
    candidate_symbols: list[str],
    store: HistoricalPriceStore,
    cfg: RiskConfig,
    benchmark_symbol: str = "SPY",
    vol_window_days: int = 20,
    atr_window_days: int = 14,
) -> dict:
```

to:

```python
def cmd_backtest_run(
    run_id: str,
    base_dir: Path,
    starting_cash: float,
    start: date,
    end: date,
    candidate_symbols: list[str],
    candidate_sectors: dict[str, str],
    store: HistoricalPriceStore,
    cfg: RiskConfig,
    benchmark_symbol: str = "SPY",
    vol_window_days: int = 20,
    atr_window_days: int = 14,
) -> dict:
```

Then, within the same function, change the "3. Entries" block from:

```python
                cash, positions_value = _total_equity(state, store, today)
                total_equity = cash + positions_value
                max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)
                proposed_value = min(max_value, state.cash)
                decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg)
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry",
                )
```

to:

```python
                cash, positions_value = _total_equity(state, store, today)
                total_equity = cash + positions_value
                max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)
                proposed_value = min(max_value, state.cash)
                sector = candidate_sectors.get(symbol)
                decision = evaluate_buy(state, symbol, proposed_value, total_equity, cfg, sector)
                if not decision.approved:
                    continue
                qty = math.floor(proposed_value / price)
                if qty <= 0:
                    continue

                commands.cmd_record_fill(
                    paths.ledger, paths.trade_log, starting_cash, "buy", symbol, qty, price, today,
                    "backtest entry", sector,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_backtest_commands.py -v`
Expected: PASS (all existing + 1 new integration test)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: FAIL only in `tests/test_cli.py` (unrelated, fixed in Task 7 — `cli.py`'s calls to `build_universe` and `cmd_backtest_run` still use the old signatures). Confirm everything else is green.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/backtest_commands.py tests/test_backtest_commands.py
git commit -m "feat: wire sector-concentration checks into the backtest entries loop"
```

---

### Task 7: `cli.py` wiring — `--sector` flags, universe output, backtest run

**Files:**
- Modify: `robinhood_bot/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh)` (Task 2); `cmd_risk_check(..., sector)`, `cmd_record_fill(..., sector)` (Task 5); `cmd_backtest_risk_check(..., sector)`, `cmd_backtest_record_fill(..., sector)`, `cmd_backtest_run(..., candidate_sectors, ...)` (Task 6).
- Produces: `SECTOR_CACHE_PATH` constant; `--sector` CLI flag on live and backtest `risk-check`/`record-fill`; `"sector"` field in `universe` command output.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, replace `test_cli_universe_command_prints_json` with:

```python
def test_cli_universe_command_prints_json(monkeypatch, capsys):
    fake_candidates = [
        universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0, sector="Technology"),
    ]

    def fake_build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh):
        return fake_candidates

    monkeypatch.setattr(cli, "build_universe", fake_build_universe)

    exit_code = cli.main(["universe"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidates"][0]["symbol"] == "AAPL"
    assert output["candidates"][0]["combined_rank"] == 1.0
    assert output["candidates"][0]["sector"] == "Technology"
```

Replace `test_cli_backtest_run_command_delegates_to_backtest_commands` with:

```python
def test_cli_backtest_run_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    fake_candidates = [universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0, sector="Technology")]
    monkeypatch.setattr(
        cli, "build_universe", lambda client, cache_path, sector_cache_path, cfg, today: fake_candidates
    )

    captured = {}

    def fake_cmd_backtest_run(
        run_id, base_dir, starting_cash, start, end, candidate_symbols, candidate_sectors, store, cfg,
        benchmark_symbol,
    ):
        captured["candidate_symbols"] = candidate_symbols
        captured["candidate_sectors"] = candidate_sectors
        return {"run_id": run_id, "trading_days": 0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_run", fake_cmd_backtest_run)

    exit_code = cli.main(["backtest", "run", "--run", "run1", "--start", "2026-01-01", "--end", "2026-01-05"])

    assert exit_code == 0
    assert captured["candidate_symbols"] == ["AAPL"]
    assert captured["candidate_sectors"] == {"AAPL": "Technology"}
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run1"
```

Append two new tests at the end of the file:

```python
def test_cli_risk_check_buy_passes_sector_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg, sector=None):
        captured["sector"] = sector
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main([
        "risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}", "--sector", "Technology",
    ])

    assert exit_code == 0
    assert captured["sector"] == "Technology"


def test_cli_backtest_risk_check_passes_sector_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    captured = {}

    def fake_cmd_backtest_risk_check(
        run_id, base_dir, starting_cash, action, symbol, proposed_value, prices, cfg, sector=None,
    ):
        captured["sector"] = sector
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_risk_check", fake_cmd_backtest_risk_check)

    exit_code = cli.main([
        "backtest", "risk-check", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--value", "500", "--prices-json", "{}", "--sector", "Financials",
    ])

    assert exit_code == 0
    assert captured["sector"] == "Financials"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: FAIL — `TypeError` on the monkeypatched `build_universe`/`cmd_backtest_run` signature mismatches, and `SystemExit`/argparse error on the unrecognized `--sector` flag for the two new tests.

- [ ] **Step 3: Write minimal implementation**

In `robinhood_bot/cli.py`, add the new constant directly after `UNIVERSE_CACHE_PATH`:

```python
LEDGER_PATH = Path("data/ledger.json")
TRADE_LOG_PATH = Path("data/trade_log.csv")
UNIVERSE_CACHE_PATH = Path("data/universe_cache.json")
SECTOR_CACHE_PATH = Path("data/sector_cache.json")
BACKTEST_BASE_DIR = Path("data/backtests")
```

In `_dispatch_backtest`, change the `"risk-check"` and `"record-fill"` branches from:

```python
    if args.backtest_command == "risk-check":
        return backtest_commands.cmd_backtest_risk_check(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg,
        )
    if args.backtest_command == "record-fill":
        return backtest_commands.cmd_backtest_record_fill(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, date.fromisoformat(args.asof), args.reason,
        )
```

to:

```python
    if args.backtest_command == "risk-check":
        return backtest_commands.cmd_backtest_risk_check(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
        )
    if args.backtest_command == "record-fill":
        return backtest_commands.cmd_backtest_record_fill(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, date.fromisoformat(args.asof), args.reason, sector=args.sector,
        )
```

Change the `"run"` branch from:

```python
    if args.backtest_command == "run":
        store = _build_price_store()
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, UniverseConfig(), date.today(),
        )
        return backtest_commands.cmd_backtest_run(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, date.fromisoformat(args.start),
            date.fromisoformat(args.end), [c.symbol for c in candidates], store, cfg,
            BENCHMARK_SYMBOL,
        )
```

to:

```python
    if args.backtest_command == "run":
        store = _build_price_store()
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, SECTOR_CACHE_PATH, UniverseConfig(), date.today(),
        )
        candidate_sectors = {c.symbol: c.sector for c in candidates if c.sector is not None}
        return backtest_commands.cmd_backtest_run(
            args.run, BACKTEST_BASE_DIR, STARTING_CASH, date.fromisoformat(args.start),
            date.fromisoformat(args.end), [c.symbol for c in candidates], candidate_sectors, store, cfg,
            BENCHMARK_SYMBOL,
        )
```

In `main()`, add `--sector` to the live `risk-check` and `record-fill` parsers — change:

```python
    p_risk = sub.add_parser("risk-check")
    p_risk.add_argument("action", choices=["buy", "sell"])
    p_risk.add_argument("symbol")
    p_risk.add_argument("--value", type=float, default=0.0)
    p_risk.add_argument("--prices-json", default=None)

    p_fill = sub.add_parser("record-fill")
    p_fill.add_argument("action", choices=["buy", "sell"])
    p_fill.add_argument("symbol")
    p_fill.add_argument("--qty", type=float, required=True)
    p_fill.add_argument("--price", type=float, required=True)
    p_fill.add_argument("--reason", default="")
```

to:

```python
    p_risk = sub.add_parser("risk-check")
    p_risk.add_argument("action", choices=["buy", "sell"])
    p_risk.add_argument("symbol")
    p_risk.add_argument("--value", type=float, default=0.0)
    p_risk.add_argument("--prices-json", default=None)
    p_risk.add_argument("--sector", default=None)

    p_fill = sub.add_parser("record-fill")
    p_fill.add_argument("action", choices=["buy", "sell"])
    p_fill.add_argument("symbol")
    p_fill.add_argument("--qty", type=float, required=True)
    p_fill.add_argument("--price", type=float, required=True)
    p_fill.add_argument("--reason", default="")
    p_fill.add_argument("--sector", default=None)
```

Add `--sector` to the backtest `risk-check` and `record-fill` sub-parsers — change:

```python
    p_bt_risk = backtest_sub.add_parser("risk-check")
    p_bt_risk.add_argument("action", choices=["buy", "sell"])
    p_bt_risk.add_argument("symbol")
    p_bt_risk.add_argument("--run", required=True)
    p_bt_risk.add_argument("--asof", required=True)
    p_bt_risk.add_argument("--value", type=float, default=0.0)
    p_bt_risk.add_argument("--prices-json", default=None)

    p_bt_fill = backtest_sub.add_parser("record-fill")
    p_bt_fill.add_argument("action", choices=["buy", "sell"])
    p_bt_fill.add_argument("symbol")
    p_bt_fill.add_argument("--run", required=True)
    p_bt_fill.add_argument("--asof", required=True)
    p_bt_fill.add_argument("--qty", type=float, required=True)
    p_bt_fill.add_argument("--price", type=float, required=True)
    p_bt_fill.add_argument("--reason", default="")
```

to:

```python
    p_bt_risk = backtest_sub.add_parser("risk-check")
    p_bt_risk.add_argument("action", choices=["buy", "sell"])
    p_bt_risk.add_argument("symbol")
    p_bt_risk.add_argument("--run", required=True)
    p_bt_risk.add_argument("--asof", required=True)
    p_bt_risk.add_argument("--value", type=float, default=0.0)
    p_bt_risk.add_argument("--prices-json", default=None)
    p_bt_risk.add_argument("--sector", default=None)

    p_bt_fill = backtest_sub.add_parser("record-fill")
    p_bt_fill.add_argument("action", choices=["buy", "sell"])
    p_bt_fill.add_argument("symbol")
    p_bt_fill.add_argument("--run", required=True)
    p_bt_fill.add_argument("--asof", required=True)
    p_bt_fill.add_argument("--qty", type=float, required=True)
    p_bt_fill.add_argument("--price", type=float, required=True)
    p_bt_fill.add_argument("--reason", default="")
    p_bt_fill.add_argument("--sector", default=None)
```

Change the live `risk-check`/`record-fill` dispatch in `main()` from:

```python
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason,
        )
```

to:

```python
    elif args.command == "risk-check":
        result = commands.cmd_risk_check(
            LEDGER_PATH, STARTING_CASH, args.action, args.symbol, args.value,
            _parse_prices(args.prices_json), cfg, sector=args.sector,
        )
    elif args.command == "record-fill":
        result = commands.cmd_record_fill(
            LEDGER_PATH, TRADE_LOG_PATH, STARTING_CASH, args.action, args.symbol,
            args.qty, args.price, today, args.reason, sector=args.sector,
        )
```

Change the live `universe` dispatch (the final `else` branch) from:

```python
    else:
        universe_cfg = UniverseConfig()
        if args.mode:
            universe_cfg.ranking_mode = args.mode
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, universe_cfg, today, args.refresh
        )
        result = {
            "candidates": [
                {
                    "symbol": c.symbol,
                    "category": c.category,
                    "market_cap": c.market_cap,
                    "realized_vol": c.realized_vol,
                    "atr_pct": c.atr_pct,
                    "combined_rank": c.combined_rank,
                }
                for c in candidates
            ]
        }
```

to:

```python
    else:
        universe_cfg = UniverseConfig()
        if args.mode:
            universe_cfg.ranking_mode = args.mode
        candidates = build_universe(
            LiveMarketDataClient(), UNIVERSE_CACHE_PATH, SECTOR_CACHE_PATH, universe_cfg, today, args.refresh
        )
        result = {
            "candidates": [
                {
                    "symbol": c.symbol,
                    "category": c.category,
                    "market_cap": c.market_cap,
                    "realized_vol": c.realized_vol,
                    "atr_pct": c.atr_pct,
                    "combined_rank": c.combined_rank,
                    "sector": c.sector,
                }
                for c in candidates
            ]
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: PASS (all existing + 2 new tests)

- [ ] **Step 5: Run the full suite**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, all tests green, no remaining failures from any prior task.

- [ ] **Step 6: Commit**

```bash
git add robinhood_bot/cli.py tests/test_cli.py
git commit -m "feat: wire --sector flag and sector-aware universe/backtest-run through the CLI"
```

---

### Task 8: `SKILL.md` documentation updates

**Files:**
- Modify: `.claude/skills/robinhood-trading/SKILL.md`

**Interfaces:**
- Consumes: nothing new — this task only documents behavior already implemented in Tasks 1-7.
- Produces: nothing code-facing; no tests (matches this repo's existing precedent of doc-only SKILL.md updates with no automated coverage).

- [ ] **Step 1: Update Step 2 (universe) to mention sector**

In `.claude/skills/robinhood-trading/SKILL.md`, change:

```
## Step 2 — Get the ranked universe

```
python -m robinhood_bot.cli universe
```

This uses a weekly-cached membership list by default (fast). Only pass
`--refresh` if explicitly asked to force a refresh.
```

to:

```
## Step 2 — Get the ranked universe

```
python -m robinhood_bot.cli universe
```

This uses a weekly-cached membership list by default (fast). Only pass
`--refresh` if explicitly asked to force a refresh. Each candidate's
`sector` field (its GICS sector, or `null` for the three leveraged funds)
is needed later in Step 7 when gating a BUY — no separate lookup is
required.
```

- [ ] **Step 2: Update Step 7 (gate) to require `--sector` on buys**

Change:

```
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
```

to:

```
```
python -m robinhood_bot.cli risk-check buy SYMBOL --value <proposed dollar amount> --sector <symbol's sector from Step 2/Step 3 candidate data> --prices-json "<fresh quotes>"
python -m robinhood_bot.cli risk-check sell SYMBOL --prices-json "<fresh quotes>"
```

- If `"approved": false`, **do not execute this trade.** Read `"reason"`
  and either propose a smaller size / different symbol, or fall back to
  HOLD. Never override a rejection.
- A BUY is rejected if you already hold an active position in the same
  `--sector` (default limit: 1 position per sector) — the rejection
  `"reason"` names the sector; treat it exactly like any other
  rejection, never override it.
- For an approved BUY, `"max_position_value"` is the ceiling. Compute a
  whole-share quantity: `floor(min(proposed_value, max_position_value) /
  fresh_quote_price)`. You may propose fewer shares than the ceiling
  allows.
```

- [ ] **Step 3: Update Step 8 (execute) to pass `--sector` on the paper buy fill**

Change:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote price> --reason "<why>"
```
```

to:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <n> --price <fresh quote price> --sector <same sector passed to Step 7's risk-check> --reason "<why>"
python -m robinhood_bot.cli record-fill sell SYMBOL --qty <held qty> --price <fresh quote price> --reason "<why>"
```
```

Also change the live-mode fill example directly below it, from:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual fill price> --reason "<why>"
```
```

to:

```
```
python -m robinhood_bot.cli record-fill buy SYMBOL --qty <actual filled qty> --price <actual fill price> --sector <same sector passed to Step 7's risk-check> --reason "<why>"
```
```

- [ ] **Step 4: Update the Backtest Mode section's Steps 7-8 bullet**

Change:

```
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --value <proposed dollar amount, for buys> --prices-json "<quotes>"`,
  then on approval, `python -m robinhood_bot.cli
  backtest record-fill {buy|sell} SYMBOL --run RUN_ID --asof <simulated
  date> --qty <n> --price <quote price> --reason "<why>"`. There is no
  live-order-placement call in this mode, ever.
```

to:

```
- **Steps 7-8 (gate and execute):** `python -m robinhood_bot.cli backtest
  risk-check {buy|sell} SYMBOL --run RUN_ID --asof <simulated date>
  --value <proposed dollar amount, for buys> --sector <symbol's sector,
  for buys> --prices-json "<quotes>"`, then on approval, `python -m
  robinhood_bot.cli backtest record-fill {buy|sell} SYMBOL --run RUN_ID
  --asof <simulated date> --qty <n> --price <quote price> --sector
  <same sector, for buys> --reason "<why>"`. There is no
  live-order-placement call in this mode, ever.
```

- [ ] **Step 5: Verify by reading the file back**

Re-read `.claude/skills/robinhood-trading/SKILL.md` in full and confirm all four edits landed cleanly and nothing else changed.

- [ ] **Step 6: Run the full suite one more time**

Run: `D:\aiworkspace\robinhood-bot\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (doc-only change, no test impact — this is the final confirmation before finishing the branch).

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/robinhood-trading/SKILL.md
git commit -m "docs: document the --sector flag and sector-concentration rejection in the trading skill"
```
