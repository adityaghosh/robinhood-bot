from datetime import date

from robinhood_bot.backtest_data import (
    HistoricalBar,
    HistoricalPriceStore,
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


def _bars(symbol_dates_closes):
    return [HistoricalBar(d, c, c + 1, c - 1, c) for d, c in symbol_dates_closes]


class FakeHistoricalDataFetcher:
    def __init__(self, bars_by_symbol=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.calls = []

    def fetch_history(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return [b for b in self.bars_by_symbol.get(symbol, []) if start <= b.date <= end]


def test_get_close_returns_price_for_known_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 102.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    assert store.get_close("AAPL", date(2026, 1, 5)) == 102.0


def test_get_close_returns_none_for_missing_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    assert store.get_close("AAPL", date(2026, 1, 3)) is None


def test_repeated_query_within_cached_range_does_not_refetch(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 102.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    store.get_close("AAPL", date(2026, 1, 2))
    store.get_close("AAPL", date(2026, 1, 5))

    assert len(fetcher.calls) == 1


def test_cache_persists_across_store_instances(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 102.0)]),
    })
    store_a = HistoricalPriceStore(fetcher, tmp_path)
    store_a.get_close("AAPL", date(2026, 1, 5))

    store_b = HistoricalPriceStore(fetcher, tmp_path)
    price = store_b.get_close("AAPL", date(2026, 1, 5))

    assert price == 102.0
    assert len(fetcher.calls) == 1
