from datetime import date

import pytest

from robinhood_bot.universe import (
    Candidate,
    UniverseConfig,
    finalize_candidates,
    is_bullish_ma_trend,
    percentile_ranks,
    rank_by_scan,
    relative_strength_index,
)


def test_universe_config_defaults():
    cfg = UniverseConfig()
    assert cfg.leveraged_funds == ["TQQQ", "UPRO"]
    assert cfg.rsi_window_days == 14
    assert cfg.ma_short_window_days == 5
    assert cfg.ma_long_window_days == 20
    assert cfg.golden_cross_short_window_days == 50
    assert cfg.golden_cross_long_window_days == 200
    assert cfg.growth_lookback_quarters == 5
    assert cfg.growth_filter_buffer == 40
    assert cfg.leveraged_combined_rank == 0.5


def test_candidate_fields():
    candidate = Candidate(
        symbol="AAPL", category="scanned", market_cap=3.0e12, pct_change=2.0,
        combined_rank=0.9, sector="Technology", rsi=62.0,
        ma_trend_bullish=True, golden_cross_bullish=True,
    )
    assert candidate.symbol == "AAPL"
    assert candidate.combined_rank == 0.9
    assert candidate.sector == "Technology"


def test_relative_strength_index_insufficient_data_is_neutral():
    assert relative_strength_index([100.0, 101.0, 102.0]) == 50.0
    assert relative_strength_index([]) == 50.0


def test_relative_strength_index_all_gains_is_100():
    closes = [100.0 + i for i in range(15)]
    assert relative_strength_index(closes) == pytest.approx(100.0)


def test_relative_strength_index_all_losses_is_zero():
    closes = [114.0 - i for i in range(15)]
    assert relative_strength_index(closes) == pytest.approx(0.0)


def test_relative_strength_index_mixed_known_value():
    closes = [100.0, 102.0, 101.0, 103.0, 102.0, 104.0, 103.0, 105.0, 104.0, 106.0, 105.0, 107.0, 106.0, 108.0, 107.0]
    assert relative_strength_index(closes) == pytest.approx(66.666666, rel=1e-4)


def test_relative_strength_index_uses_wilder_smoothing_over_full_history():
    closes = [
        103.0, 105.0, 107.0, 110.0, 109.0, 111.0, 113.0, 115.0, 118.0, 120.0,
        123.0, 126.0, 125.5, 127.5, 127.0, 128.5, 130.5, 132.5, 134.5, 136.5,
        138.5, 138.0, 137.5, 139.5, 139.0, 141.0, 144.0, 147.0, 150.0, 149.5,
        151.0, 153.0, 154.5, 154.0, 153.0, 151.0, 149.0, 151.0, 149.0, 147.0,
    ]
    result = relative_strength_index(closes)
    assert result == pytest.approx(64.0932449136437, rel=1e-9)
    assert result != pytest.approx(61.53846153846153, rel=1e-4)


def test_is_bullish_ma_trend_insufficient_data_is_none():
    assert is_bullish_ma_trend([100.0] * 10) is None
    assert is_bullish_ma_trend([]) is None


def test_is_bullish_ma_trend_true_when_short_average_above_long_average():
    closes = [90.0] * 15 + [110.0] * 5
    assert is_bullish_ma_trend(closes) is True


def test_is_bullish_ma_trend_false_when_short_average_at_or_below_long_average():
    closes = [110.0] * 15 + [90.0] * 5
    assert is_bullish_ma_trend(closes) is False


def test_percentile_ranks_empty_input():
    assert percentile_ranks({}) == {}


def test_percentile_ranks_single_entry_is_one():
    assert percentile_ranks({"A": 5.0}) == {"A": 1.0}


def test_percentile_ranks_orders_ascending():
    result = percentile_ranks({"A": 1.0, "B": 3.0, "C": 2.0})
    assert result == {"A": 0.0, "C": 0.5, "B": 1.0}


def test_rank_by_scan_computes_combined_rank_from_pct_change_and_rsi():
    scan_rows = [
        {"symbol": "A", "market_cap": 1.0e11, "pct_change": 1.0, "rsi": 40.0},
        {"symbol": "B", "market_cap": 2.0e11, "pct_change": 5.0, "rsi": 60.0},
        {"symbol": "C", "market_cap": 3.0e11, "pct_change": 3.0, "rsi": 50.0},
    ]

    ranked = rank_by_scan(scan_rows, UniverseConfig())

    assert [r["symbol"] for r in ranked] == ["B", "C", "A"]
    assert ranked[0]["combined_rank"] == 1.0
    assert ranked[1]["combined_rank"] == 0.5
    assert ranked[2]["combined_rank"] == 0.0


def test_rank_by_scan_preserves_other_row_fields():
    scan_rows = [{"symbol": "A", "market_cap": 1.0e11, "pct_change": 1.0, "rsi": 40.0}]

    ranked = rank_by_scan(scan_rows, UniverseConfig())

    assert ranked[0]["market_cap"] == 1.0e11


def test_rank_by_scan_empty_input_returns_empty_list():
    assert rank_by_scan([], UniverseConfig()) == []


def test_finalize_candidates_attaches_ma_trend_when_closes_present():
    rows = [{
        "symbol": "AAPL", "category": "scanned", "market_cap": 3.0e12, "pct_change": 2.0,
        "combined_rank": 0.8, "sector": "Technology", "rsi": 62.0,
    }]
    closes = [90.0] * 15 + [110.0] * 5

    candidates = finalize_candidates(rows, {"AAPL": closes}, UniverseConfig())

    assert candidates[0].symbol == "AAPL"
    assert candidates[0].combined_rank == 0.8
    assert candidates[0].sector == "Technology"
    assert candidates[0].ma_trend_bullish is True


def test_finalize_candidates_attaches_golden_cross_with_sufficient_history():
    rows = [{
        "symbol": "AAPL", "category": "scanned", "market_cap": 3.0e12, "pct_change": 2.0,
        "combined_rank": 0.8, "sector": "Technology", "rsi": 62.0,
    }]
    closes = [100.0 + i * 0.1 for i in range(201)]

    candidates = finalize_candidates(rows, {"AAPL": closes}, UniverseConfig())

    assert candidates[0].golden_cross_bullish is True


def test_finalize_candidates_null_ma_trend_and_golden_cross_when_closes_missing():
    rows = [{
        "symbol": "TQQQ", "category": "leveraged", "market_cap": 0.0, "pct_change": 0.0,
        "combined_rank": 0.5, "sector": None, "rsi": 50.0,
    }]

    candidates = finalize_candidates(rows, {}, UniverseConfig())

    assert candidates[0].ma_trend_bullish is None
    assert candidates[0].golden_cross_bullish is None


def test_finalize_candidates_preserves_input_order():
    rows = [
        {"symbol": "B", "category": "scanned", "market_cap": 1.0, "pct_change": 1.0,
         "combined_rank": 0.9, "sector": "Tech", "rsi": 55.0},
        {"symbol": "A", "category": "scanned", "market_cap": 1.0, "pct_change": 1.0,
         "combined_rank": 0.7, "sector": "Tech", "rsi": 55.0},
    ]

    candidates = finalize_candidates(rows, {}, UniverseConfig())

    assert [c.symbol for c in candidates] == ["B", "A"]
