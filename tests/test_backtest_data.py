from datetime import date

from robinhood_bot.backtest_data import (
    HistoricalBar,
    SymbolCache,
    load_symbol_cache,
    save_symbol_cache,
)


def test_historical_bar_fields():
    bar = HistoricalBar(date=date(2026, 7, 1), open=99.0, high=101.0, low=98.5, close=100.0)
    assert bar.date == date(2026, 7, 1)
    assert bar.open == 99.0
    assert bar.close == 100.0


def test_load_symbol_cache_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "AAPL.json"
    assert load_symbol_cache(path) is None


def test_save_and_load_symbol_cache_round_trip(tmp_path):
    path = tmp_path / "AAPL.json"
    original = SymbolCache(
        start=date(2026, 1, 1),
        end=date(2026, 7, 1),
        bars=[
            HistoricalBar(date(2026, 1, 2), 99.0, 101.0, 98.5, 100.0),
            HistoricalBar(date(2026, 1, 3), 100.0, 102.0, 99.5, 101.0),
        ],
    )
    save_symbol_cache(path, original)
    loaded = load_symbol_cache(path)

    assert loaded.start == date(2026, 1, 1)
    assert loaded.end == date(2026, 7, 1)
    assert loaded.bars[0].date == date(2026, 1, 2)
    assert loaded.bars[1].close == 101.0
