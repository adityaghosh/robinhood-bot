from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
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
