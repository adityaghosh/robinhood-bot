from __future__ import annotations

import json
import math
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


def rank_top_by_market_cap(tickers: list[str], market_caps: dict[str, float], top_n: int) -> list[str]:
    known = [t for t in tickers if t in market_caps]
    known.sort(key=lambda t: market_caps[t], reverse=True)
    return known[:top_n]


def realized_volatility(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def average_true_range_pct(bars: list[Bar]) -> float:
    if len(bars) < 2:
        return 0.0
    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(true_ranges) / len(true_ranges)
    last_close = bars[-1].close
    return (atr / last_close) if last_close else 0.0


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda s: values[s])
    n = len(ordered)
    if n == 1:
        return {ordered[0]: 1.0}
    return {symbol: i / (n - 1) for i, symbol in enumerate(ordered)}


def refresh_membership(client: MarketDataClient, cfg: UniverseConfig) -> list[CachedMember]:
    sp500 = client.fetch_sp500_tickers()
    nasdaq = client.fetch_nasdaq100_tickers()
    all_tickers = sorted(set(sp500) | set(nasdaq))
    market_caps = client.fetch_market_caps(all_tickers)

    top_sp500 = rank_top_by_market_cap(sp500, market_caps, cfg.top_n_sp500)
    top_nasdaq = rank_top_by_market_cap(nasdaq, market_caps, cfg.top_n_nasdaq100)

    members: dict[str, CachedMember] = {}
    for ticker in top_sp500:
        members[ticker] = CachedMember(ticker, "sp500", market_caps[ticker])
    for ticker in top_nasdaq:
        if ticker not in members:
            members[ticker] = CachedMember(ticker, "nasdaq100", market_caps[ticker])
    return list(members.values())


def get_membership(
    client: MarketDataClient,
    cache_path: Path,
    cfg: UniverseConfig,
    today: date,
    force_refresh: bool = False,
) -> list[CachedMember]:
    cache = load_cache(cache_path)
    if not force_refresh and not is_cache_stale(cache, today, cfg.cache_max_age_days):
        return cache.members

    try:
        members = refresh_membership(client, cfg)
    except Exception:
        if cache is not None:
            return cache.members
        raise

    save_cache(cache_path, UniverseCache(fetched_at=today, members=members))
    return members
