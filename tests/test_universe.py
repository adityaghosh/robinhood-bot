from datetime import date, timedelta
from pathlib import Path

import pytest

from robinhood_bot.universe import (
    Bar,
    CachedMember,
    Candidate,
    UniverseCache,
    UniverseConfig,
    is_cache_stale,
    load_cache,
    save_cache,
)


def test_universe_config_defaults():
    cfg = UniverseConfig()
    assert cfg.top_n_sp500 == 100
    assert cfg.top_n_nasdaq100 == 20
    assert cfg.leveraged_funds == ["TQQQ", "UPRO", "SOXL"]
    assert cfg.realized_vol_window_days == 20
    assert cfg.atr_window_days == 14
    assert cfg.cache_max_age_days == 7
    assert cfg.ranking_mode == "both"


def test_bar_fields():
    bar = Bar(high=101.0, low=99.0, close=100.0)
    assert bar.high == 101.0
    assert bar.low == 99.0
    assert bar.close == 100.0


def test_cached_member_fields():
    member = CachedMember(symbol="AAPL", category="sp500", market_cap=3.0e12)
    assert member.symbol == "AAPL"
    assert member.category == "sp500"
    assert member.market_cap == 3.0e12


def test_universe_cache_fields():
    cache = UniverseCache(
        fetched_at=date(2026, 7, 19),
        members=[CachedMember("AAPL", "sp500", 3.0e12)],
    )
    assert cache.fetched_at == date(2026, 7, 19)
    assert cache.members[0].symbol == "AAPL"


def test_candidate_fields():
    candidate = Candidate(
        symbol="AAPL", category="sp500", market_cap=3.0e12,
        realized_vol=0.25, atr_pct=0.02, combined_rank=0.9,
    )
    assert candidate.symbol == "AAPL"
    assert candidate.combined_rank == 0.9


def test_load_cache_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "universe_cache.json"
    assert load_cache(path) is None


def test_save_and_load_cache_round_trip(tmp_path):
    path = tmp_path / "universe_cache.json"
    original = UniverseCache(
        fetched_at=date(2026, 7, 19),
        members=[
            CachedMember("AAPL", "sp500", 3.0e12),
            CachedMember("TQQQ", "leveraged", 0.0),
        ],
    )
    save_cache(path, original)
    loaded = load_cache(path)

    assert loaded.fetched_at == date(2026, 7, 19)
    assert loaded.members[0].symbol == "AAPL"
    assert loaded.members[0].market_cap == 3.0e12
    assert loaded.members[1].symbol == "TQQQ"


def test_is_cache_stale_when_cache_is_none():
    assert is_cache_stale(None, today=date(2026, 7, 19), max_age_days=7) is True


def test_is_cache_stale_at_exact_max_age_is_not_stale():
    cache = UniverseCache(fetched_at=date(2026, 7, 12), members=[])
    assert is_cache_stale(cache, today=date(2026, 7, 19), max_age_days=7) is False


def test_is_cache_stale_past_max_age_is_stale():
    cache = UniverseCache(fetched_at=date(2026, 7, 11), members=[])
    assert is_cache_stale(cache, today=date(2026, 7, 19), max_age_days=7) is True


from robinhood_bot.universe import rank_top_by_market_cap


def test_rank_top_by_market_cap_orders_descending_and_truncates():
    tickers = ["A", "B", "C"]
    market_caps = {"A": 100.0, "B": 300.0, "C": 200.0}
    assert rank_top_by_market_cap(tickers, market_caps, top_n=2) == ["B", "C"]


def test_rank_top_by_market_cap_excludes_tickers_without_market_cap():
    tickers = ["A", "B", "D"]
    market_caps = {"A": 100.0, "B": 300.0}
    assert rank_top_by_market_cap(tickers, market_caps, top_n=5) == ["B", "A"]


from robinhood_bot.universe import realized_volatility


def test_realized_volatility_of_constant_closes_is_zero():
    assert realized_volatility([100.0, 100.0, 100.0, 100.0]) == 0.0


def test_realized_volatility_too_few_points_is_zero():
    assert realized_volatility([100.0]) == 0.0
    assert realized_volatility([]) == 0.0


def test_realized_volatility_known_value():
    closes = [100.0, 102.0, 98.0, 101.0, 99.0]
    assert realized_volatility(closes) == pytest.approx(0.5246239382982052)


from robinhood_bot.universe import average_true_range_pct


def test_average_true_range_pct_too_few_bars_is_zero():
    assert average_true_range_pct([]) == 0.0
    assert average_true_range_pct([Bar(101.0, 99.0, 100.0)]) == 0.0


def test_average_true_range_pct_known_value():
    bars = [
        Bar(high=101.0, low=99.0, close=100.0),
        Bar(high=103.0, low=100.0, close=102.0),
        Bar(high=102.5, low=99.5, close=101.0),
        Bar(high=104.0, low=100.5, close=103.0),
    ]
    assert average_true_range_pct(bars) == pytest.approx(0.030744336569579287)


from robinhood_bot.universe import percentile_ranks


def test_percentile_ranks_empty_input():
    assert percentile_ranks({}) == {}


def test_percentile_ranks_single_entry_is_one():
    assert percentile_ranks({"A": 5.0}) == {"A": 1.0}


def test_percentile_ranks_orders_ascending():
    result = percentile_ranks({"A": 1.0, "B": 3.0, "C": 2.0})
    assert result == {"A": 0.0, "C": 0.5, "B": 1.0}


from robinhood_bot.universe import get_membership, refresh_membership


class FakeMarketDataClient:
    def __init__(self, sp500=None, nasdaq100=None, market_caps=None, bars=None, raise_on_fetch=False):
        self.sp500 = sp500 or []
        self.nasdaq100 = nasdaq100 or []
        self.market_caps = market_caps or {}
        self.bars = bars or {}
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


def test_refresh_membership_dedupes_overlap_preferring_sp500_category():
    client = FakeMarketDataClient(
        sp500=["A", "B", "C"],
        nasdaq100=["C", "D"],
        market_caps={"A": 100.0, "B": 300.0, "C": 200.0, "D": 50.0},
    )
    cfg = UniverseConfig(top_n_sp500=2, top_n_nasdaq100=2)

    members = refresh_membership(client, cfg)

    by_symbol = {m.symbol: m for m in members}
    assert set(by_symbol) == {"B", "C", "D"}
    assert by_symbol["C"].category == "sp500"
    assert by_symbol["B"].market_cap == 300.0


def test_get_membership_returns_cached_members_without_network_when_fresh(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    save_cache(cache_path, UniverseCache(fetched_at=today, members=[CachedMember("AAPL", "sp500", 3.0e12)]))
    client = FakeMarketDataClient()
    cfg = UniverseConfig(cache_max_age_days=7)

    members = get_membership(client, cache_path, cfg, today, force_refresh=False)

    assert [m.symbol for m in members] == ["AAPL"]
    assert client.calls == []


def test_get_membership_refreshes_and_saves_when_cache_missing(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    client = FakeMarketDataClient(sp500=["A", "B"], nasdaq100=[], market_caps={"A": 100.0, "B": 300.0})
    cfg = UniverseConfig(top_n_sp500=2, top_n_nasdaq100=2)

    members = get_membership(client, cache_path, cfg, today, force_refresh=False)

    assert {m.symbol for m in members} == {"A", "B"}
    reloaded = load_cache(cache_path)
    assert reloaded.fetched_at == today
    assert {m.symbol for m in reloaded.members} == {"A", "B"}


def test_get_membership_force_refresh_ignores_fresh_cache(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    save_cache(cache_path, UniverseCache(fetched_at=today, members=[CachedMember("OLD", "sp500", 1.0)]))
    client = FakeMarketDataClient(sp500=["NEW"], nasdaq100=[], market_caps={"NEW": 500.0})
    cfg = UniverseConfig(top_n_sp500=1, top_n_nasdaq100=1)

    members = get_membership(client, cache_path, cfg, today, force_refresh=True)

    assert [m.symbol for m in members] == ["NEW"]
    assert "sp500" in client.calls


def test_get_membership_falls_back_to_existing_cache_on_fetch_failure(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    stale_date = date(2026, 7, 1)
    today = date(2026, 7, 19)
    save_cache(cache_path, UniverseCache(fetched_at=stale_date, members=[CachedMember("OLD", "sp500", 1.0)]))
    client = FakeMarketDataClient(raise_on_fetch=True)
    cfg = UniverseConfig(cache_max_age_days=7)

    members = get_membership(client, cache_path, cfg, today, force_refresh=False)

    assert [m.symbol for m in members] == ["OLD"]


def test_get_membership_raises_on_fetch_failure_with_no_cache(tmp_path):
    cache_path = tmp_path / "universe_cache.json"
    today = date(2026, 7, 19)
    client = FakeMarketDataClient(raise_on_fetch=True)
    cfg = UniverseConfig()

    with pytest.raises(RuntimeError):
        get_membership(client, cache_path, cfg, today, force_refresh=False)
