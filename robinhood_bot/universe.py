from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
