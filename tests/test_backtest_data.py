from datetime import date

import pytest

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


def test_repeated_query_for_same_date_does_not_refetch(tmp_path):
    # get_close/get_ohlc each request only the exact single day passed in (no
    # buffer — that's added by get_ohlc_window in Task 3), so two *different*
    # dates each trigger their own fetch; only re-querying the same date is
    # guaranteed to hit the cache at this stage.
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    store.get_close("AAPL", date(2026, 1, 2))
    store.get_close("AAPL", date(2026, 1, 2))

    assert len(fetcher.calls) == 1


def test_query_for_new_date_fetches_and_merges_with_existing_cache(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 102.0)]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    store.get_close("AAPL", date(2026, 1, 2))
    store.get_close("AAPL", date(2026, 1, 5))

    assert len(fetcher.calls) == 2
    assert fetcher.calls[1] == ("AAPL", date(2026, 1, 2), date(2026, 1, 5))
    # Both dates remain retrievable after the merge.
    assert store.get_close("AAPL", date(2026, 1, 2)) == 100.0
    assert store.get_close("AAPL", date(2026, 1, 5)) == 102.0


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


def test_get_closes_window_returns_trailing_closes_ending_at_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([
            (date(2026, 1, 2), 100.0),
            (date(2026, 1, 3), 101.0),
            (date(2026, 1, 4), 102.0),
            (date(2026, 1, 5), 103.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    closes = store.get_closes_window("AAPL", date(2026, 1, 4), window_days=2)

    assert closes == [101.0, 102.0]


def test_get_closes_window_never_includes_dates_after_end_date(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([
            (date(2026, 1, 2), 100.0),
            (date(2026, 1, 3), 101.0),
            (date(2026, 1, 4), 102.0),
            (date(2026, 1, 5), 103.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    closes = store.get_closes_window("AAPL", date(2026, 1, 3), window_days=10)

    assert closes == [100.0, 101.0]


def test_get_closes_window_excludes_future_bars_already_present_in_cache(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "AAPL": _bars([
            (date(2026, 1, 2), 100.0),
            (date(2026, 1, 3), 101.0),
            (date(2026, 1, 4), 102.0),
            (date(2026, 1, 5), 103.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)
    # Warm the cache with the full range first, as `backtest run` would do.
    store.get_ohlc_window("AAPL", date(2026, 1, 5), window_days=10)

    closes = store.get_closes_window("AAPL", date(2026, 1, 3), window_days=10)

    assert closes == [100.0, 101.0]


def test_trading_days_excludes_weekends_via_benchmark_dates(tmp_path):
    fetcher = FakeHistoricalDataFetcher({
        "SPY": _bars([
            (date(2026, 1, 2), 400.0),  # Friday
            (date(2026, 1, 5), 402.0),  # Monday
            (date(2026, 1, 6), 403.0),
        ]),
    })
    store = HistoricalPriceStore(fetcher, tmp_path)

    days = store.trading_days("SPY", date(2026, 1, 1), date(2026, 1, 6))

    assert days == [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]


def test_trading_days_raises_when_benchmark_fetch_fails(tmp_path):
    class FailingFetcher:
        def fetch_history(self, symbol, start, end):
            raise RuntimeError("network error")

    store = HistoricalPriceStore(FailingFetcher(), tmp_path)

    with pytest.raises(RuntimeError):
        store.trading_days("SPY", date(2026, 1, 1), date(2026, 1, 6))
