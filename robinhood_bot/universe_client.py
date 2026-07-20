# robinhood_bot/universe_client.py
from __future__ import annotations

import io
import urllib.request
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from .backtest_data import HistoricalBar
from .universe import Bar

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"


def clean_ticker_for_yfinance(symbol: str) -> str:
    return symbol.replace(".", "-")


def _fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")


class LiveMarketDataClient:
    def fetch_sp500_tickers(self) -> list[str]:
        # NOTE: pd.read_html must receive a file-like object (io.StringIO),
        # not a raw str -- lxml's parse() treats a plain str argument as a
        # filename/URL rather than literal HTML content, raising
        # FileNotFoundError on the full page markup.
        tables = pd.read_html(io.StringIO(_fetch_html(SP500_WIKI_URL)))
        symbols = tables[0]["Symbol"].tolist()
        return [clean_ticker_for_yfinance(s) for s in symbols]

    def fetch_nasdaq100_tickers(self) -> list[str]:
        tables = pd.read_html(io.StringIO(_fetch_html(NASDAQ100_WIKI_URL)))
        tickers = tables[0]["Ticker"].tolist()
        return [clean_ticker_for_yfinance(t) for t in tickers]

    def fetch_market_caps(self, tickers: list[str]) -> dict[str, float]:
        market_caps: dict[str, float] = {}
        for ticker in tickers:
            try:
                # NOTE: fast_info has no project-controlled timeout knob --
                # yf.Ticker() doesn't accept a timeout kwarg, and internally
                # fast_info's data fetches (yfinance.data.YfData.get) default
                # to a hardcoded 30s timeout with no way to override it from
                # this call site. Documented limitation, not an oversight.
                info = yf.Ticker(ticker).fast_info
                market_cap = info.get("market_cap") or info.get("marketCap")
            except Exception:
                market_cap = None
            if market_cap:
                market_caps[ticker] = float(market_cap)
        return market_caps

    def fetch_sector(self, ticker: str) -> str | None:
        try:
            # NOTE: deliberately reads GICS *industry* (e.g. "Semiconductors",
            # "Computer Hardware"), not the broader "sector" (e.g.
            # "Technology") -- the concentration check needs sub-industry
            # granularity so a semiconductor position doesn't block an
            # unrelated Technology name (e.g. IT services) from being held
            # at the same time. Neither is available on fast_info -- only
            # the full (slower) .info property exposes this classification.
            info = yf.Ticker(ticker).info
            sector = info.get("industry")
        except Exception:
            return None
        return sector if sector else None

    def fetch_daily_bars(self, ticker: str, lookback_days: int) -> list[Bar]:
        try:
            history = yf.Ticker(ticker).history(
                period=f"{lookback_days + 5}d", timeout=15
            )
        except Exception:
            return []
        if history.empty:
            return []
        bars = [
            Bar(high=float(row.High), low=float(row.Low), close=float(row.Close))
            for row in history.itertuples()
        ]
        return bars[-lookback_days:]


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
