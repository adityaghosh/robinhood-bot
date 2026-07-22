# robinhood_bot/universe_client.py
from __future__ import annotations

from datetime import date, timedelta

import yfinance as yf

from .backtest_data import HistoricalBar


class LiveHistoricalDataFetcher:
    def fetch_history(self, symbol: str, start: date, end: date) -> list[HistoricalBar]:
        try:
            # yfinance's `end` is exclusive, so add a day to make our own
            # [start, end] contract inclusive of `end`.
            history = yf.Ticker(symbol).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), timeout=15
            )
        except Exception:
            return []
        if history.empty:
            return []
        return [
            HistoricalBar(
                date=row.Index.date(),
                open=float(row.Open),
                high=float(row.High),
                low=float(row.Low),
                close=float(row.Close),
            )
            for row in history.itertuples()
        ]
