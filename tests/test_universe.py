from datetime import date, timedelta
from pathlib import Path

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
