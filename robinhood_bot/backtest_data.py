from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol


@dataclass
class HistoricalBar:
    date: date
    open: float
    high: float
    low: float
    close: float


class HistoricalDataFetcher(Protocol):
    def fetch_history(self, symbol: str, start: date, end: date) -> list[HistoricalBar]: ...


@dataclass
class SymbolCache:
    start: date
    end: date
    bars: list[HistoricalBar]


def _bar_to_dict(bar: HistoricalBar) -> dict:
    return {
        "date": bar.date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
    }


def _bar_from_dict(data: dict) -> HistoricalBar:
    return HistoricalBar(
        date=date.fromisoformat(data["date"]),
        open=data["open"],
        high=data["high"],
        low=data["low"],
        close=data["close"],
    )


def _cache_to_dict(cache: SymbolCache) -> dict:
    return {
        "start": cache.start.isoformat(),
        "end": cache.end.isoformat(),
        "bars": [_bar_to_dict(b) for b in cache.bars],
    }


def _cache_from_dict(data: dict) -> SymbolCache:
    return SymbolCache(
        start=date.fromisoformat(data["start"]),
        end=date.fromisoformat(data["end"]),
        bars=[_bar_from_dict(b) for b in data["bars"]],
    )


def load_symbol_cache(path: Path) -> SymbolCache | None:
    if not path.exists():
        return None
    with path.open("r") as f:
        return _cache_from_dict(json.load(f))


def save_symbol_cache(path: Path, cache: SymbolCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(_cache_to_dict(cache), f, indent=2)


class HistoricalPriceStore:
    def __init__(self, fetcher: HistoricalDataFetcher, cache_dir: Path):
        self._fetcher = fetcher
        self._cache_dir = cache_dir
        self._bars: dict[str, dict[date, HistoricalBar]] = {}
        self._ranges: dict[str, tuple[date, date] | None] = {}

    def _cache_path(self, symbol: str) -> Path:
        return self._cache_dir / f"{symbol}.json"

    def _ensure_range(self, symbol: str, start: date, end: date) -> None:
        if symbol not in self._bars:
            cache = load_symbol_cache(self._cache_path(symbol))
            if cache is not None:
                self._bars[symbol] = {b.date: b for b in cache.bars}
                self._ranges[symbol] = (cache.start, cache.end)
            else:
                self._bars[symbol] = {}
                self._ranges[symbol] = None

        cached_range = self._ranges[symbol]
        if cached_range is not None and cached_range[0] <= start and end <= cached_range[1]:
            return

        fetch_start = min(start, cached_range[0]) if cached_range else start
        fetch_end = max(end, cached_range[1]) if cached_range else end

        for bar in self._fetcher.fetch_history(symbol, fetch_start, fetch_end):
            self._bars[symbol][bar.date] = bar
        self._ranges[symbol] = (fetch_start, fetch_end)

        save_symbol_cache(
            self._cache_path(symbol),
            SymbolCache(start=fetch_start, end=fetch_end, bars=list(self._bars[symbol].values())),
        )

    def get_ohlc(self, symbol: str, on: date) -> HistoricalBar | None:
        self._ensure_range(symbol, on, on)
        return self._bars[symbol].get(on)

    def get_close(self, symbol: str, on: date) -> float | None:
        bar = self.get_ohlc(symbol, on)
        return bar.close if bar else None

    def get_ohlc_window(self, symbol: str, end_date: date, window_days: int) -> list[HistoricalBar]:
        # Fetch a generous calendar-day buffer so `window_days` *trading* days
        # are available even across weekends/holidays; the trailing-slice
        # below is what actually enforces no-lookahead, not this buffer.
        fetch_start = end_date - timedelta(days=window_days * 2 + 10)
        self._ensure_range(symbol, fetch_start, end_date)
        dates = sorted(d for d in self._bars[symbol] if d <= end_date)
        trailing = dates[-window_days:]
        return [self._bars[symbol][d] for d in trailing]

    def get_closes_window(self, symbol: str, end_date: date, window_days: int) -> list[float]:
        return [bar.close for bar in self.get_ohlc_window(symbol, end_date, window_days)]
