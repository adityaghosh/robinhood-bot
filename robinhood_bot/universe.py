from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class UniverseConfig:
    leveraged_funds: list[str] = field(default_factory=lambda: ["TQQQ", "UPRO"])
    rsi_window_days: int = 14
    ma_short_window_days: int = 5
    ma_long_window_days: int = 20
    golden_cross_short_window_days: int = 50
    golden_cross_long_window_days: int = 200
    growth_lookback_quarters: int = 5
    growth_filter_buffer: int = 40
    leveraged_combined_rank: float = 0.5


@dataclass
class Candidate:
    symbol: str
    category: str
    market_cap: float
    pct_change: float
    combined_rank: float
    sector: str | None = None
    rsi: float = 50.0
    ma_trend_bullish: bool | None = None
    golden_cross_bullish: bool | None = None


def relative_strength_index(closes: list[float], window_days: int = 14) -> float:
    if len(closes) < window_days + 1:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:window_days]) / window_days
    avg_loss = sum(losses[:window_days]) / window_days
    for i in range(window_days, len(changes)):
        avg_gain = (avg_gain * (window_days - 1) + gains[i]) / window_days
        avg_loss = (avg_loss * (window_days - 1) + losses[i]) / window_days
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def is_bullish_ma_trend(closes: list[float], short_window: int = 5, long_window: int = 20) -> bool | None:
    if len(closes) < long_window:
        return None
    short_avg = sum(closes[-short_window:]) / short_window
    long_avg = sum(closes[-long_window:]) / long_window
    return short_avg > long_avg


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda s: values[s])
    n = len(ordered)
    if n == 1:
        return {ordered[0]: 1.0}
    return {symbol: i / (n - 1) for i, symbol in enumerate(ordered)}


def rank_by_scan(scan_rows: list[dict], cfg: UniverseConfig) -> list[dict]:
    pct_changes = {row["symbol"]: row["pct_change"] for row in scan_rows}
    rsis = {row["symbol"]: row["rsi"] for row in scan_rows}
    pct_change_ranks = percentile_ranks(pct_changes)
    rsi_ranks = percentile_ranks(rsis)

    ranked = []
    for row in scan_rows:
        symbol = row["symbol"]
        combined_rank = (pct_change_ranks[symbol] + rsi_ranks[symbol]) / 2
        ranked.append({**row, "combined_rank": combined_rank})

    ranked.sort(key=lambda r: r["combined_rank"], reverse=True)
    return ranked


def finalize_candidates(
    rows: list[dict], closes_by_symbol: dict[str, list[float]], cfg: UniverseConfig,
) -> list[Candidate]:
    candidates = []
    for row in rows:
        closes = closes_by_symbol.get(row["symbol"])
        if closes:
            ma_trend_bullish = is_bullish_ma_trend(closes, cfg.ma_short_window_days, cfg.ma_long_window_days)
            golden_cross_bullish = is_bullish_ma_trend(
                closes, cfg.golden_cross_short_window_days, cfg.golden_cross_long_window_days
            )
        else:
            ma_trend_bullish = None
            golden_cross_bullish = None
        candidates.append(Candidate(
            symbol=row["symbol"],
            category=row["category"],
            market_cap=row["market_cap"],
            pct_change=row["pct_change"],
            combined_rank=row["combined_rank"],
            sector=row.get("sector"),
            rsi=row["rsi"],
            ma_trend_bullish=ma_trend_bullish,
            golden_cross_bullish=golden_cross_bullish,
        ))
    return candidates
