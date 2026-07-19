from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol


@dataclass
class UniverseConfig:
    top_n_sp500: int = 100
    top_n_nasdaq100: int = 20
    leveraged_funds: list[str] = field(default_factory=lambda: ["TQQQ", "UPRO", "SOXL"])
    realized_vol_window_days: int = 20
    atr_window_days: int = 14
    cache_max_age_days: int = 7
    ranking_mode: str = "both"


@dataclass
class Bar:
    high: float
    low: float
    close: float


@dataclass
class CachedMember:
    symbol: str
    category: str
    market_cap: float


@dataclass
class UniverseCache:
    fetched_at: date
    members: list[CachedMember]


@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    realized_vol: float
    atr_pct: float
    combined_rank: float


class MarketDataClient(Protocol):
    def fetch_sp500_tickers(self) -> list[str]: ...
    def fetch_nasdaq100_tickers(self) -> list[str]: ...
    def fetch_market_caps(self, tickers: list[str]) -> dict[str, float]: ...
    def fetch_daily_bars(self, ticker: str, lookback_days: int) -> list[Bar]: ...


def _cached_member_to_dict(member: CachedMember) -> dict:
    return {"symbol": member.symbol, "category": member.category, "market_cap": member.market_cap}


def _cached_member_from_dict(data: dict) -> CachedMember:
    return CachedMember(symbol=data["symbol"], category=data["category"], market_cap=data["market_cap"])


def cache_to_dict(cache: UniverseCache) -> dict:
    return {
        "fetched_at": cache.fetched_at.isoformat(),
        "members": [_cached_member_to_dict(m) for m in cache.members],
    }


def cache_from_dict(data: dict) -> UniverseCache:
    return UniverseCache(
        fetched_at=date.fromisoformat(data["fetched_at"]),
        members=[_cached_member_from_dict(m) for m in data["members"]],
    )


def load_cache(path: Path) -> UniverseCache | None:
    if not path.exists():
        return None
    with path.open("r") as f:
        return cache_from_dict(json.load(f))


def save_cache(path: Path, cache: UniverseCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(cache_to_dict(cache), f, indent=2)


def is_cache_stale(cache: UniverseCache | None, today: date, max_age_days: int) -> bool:
    if cache is None:
        return True
    return (today - cache.fetched_at).days > max_age_days
