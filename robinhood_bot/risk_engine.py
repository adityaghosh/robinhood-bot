from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .portfolio_state import Position, PositionStatus, PortfolioState


@dataclass
class RiskConfig:
    max_active_positions: int = 5
    max_bonus_active_slots: int = 2
    max_positions_per_sector: int = 1
    stop_loss_pct: float = 0.05
    weekly_profit_goal: float = 500.0
    grace_period_days: int = 5
    max_position_pct: float = 0.20
    min_position_pct: float = 0.05
    long_hold_capital_cap_pct: float = 0.30
    monthly_circuit_breaker_pct: float = 0.05
    rsi_overbought_threshold: float = 70.0
    profit_bank_band_width: float = 100.0
    profit_bank_rate_step: float = 0.25


class ExitAction(str, Enum):
    SELL = "SELL"
    PROMOTE_LONG_HOLD = "PROMOTE_LONG_HOLD"
    HOLD = "HOLD"


@dataclass
class PositionEvaluation:
    action: ExitAction
    new_status: PositionStatus
    new_underwater_since: date | None


def evaluate_position(
    position: Position, current_price: float, today: date, cfg: RiskConfig
) -> PositionEvaluation:
    pnl_pct = (current_price - position.entry_price) / position.entry_price

    if pnl_pct <= -cfg.stop_loss_pct:
        underwater_since = position.underwater_since or today
        days_underwater = (today - underwater_since).days
        if days_underwater > cfg.grace_period_days:
            return PositionEvaluation(ExitAction.PROMOTE_LONG_HOLD, PositionStatus.LONG_HOLD, None)
        return PositionEvaluation(ExitAction.HOLD, PositionStatus.WAITING, underwater_since)

    return PositionEvaluation(ExitAction.HOLD, PositionStatus.ACTIVE, None)


def current_weekly_tier(week_realized_pnl: float, cfg: RiskConfig) -> float:
    return max(0.0, (int(week_realized_pnl // cfg.weekly_profit_goal) + 1) * cfg.weekly_profit_goal)


def bonus_active_slots(prior_week_realized_pnl: float, cfg: RiskConfig) -> int:
    surplus = prior_week_realized_pnl - cfg.weekly_profit_goal
    if surplus <= 0:
        return 0
    return min(cfg.max_bonus_active_slots, int(surplus // cfg.weekly_profit_goal))


def evaluate_profit_exits(
    positions: list[Position], prices: dict[str, float], week_realized_pnl: float, cfg: RiskConfig,
) -> list[Position]:
    gains = []
    for position in positions:
        price = prices.get(position.symbol)
        if price is None:
            continue
        gain = (price - position.entry_price) * position.qty
        if gain > 0:
            gains.append((gain, position))
    gains.sort(key=lambda g: g[0], reverse=True)

    tier = current_weekly_tier(week_realized_pnl, cfg)
    to_sell = []
    running = week_realized_pnl
    for gain, position in gains:
        if running >= tier:
            break
        to_sell.append(position)
        running += gain
    return to_sell


def banked_amount(week_realized_pnl_before: float, gain: float, cfg: RiskConfig) -> float:
    if gain <= 0:
        return 0.0

    threshold = cfg.weekly_profit_goal
    width = cfg.profit_bank_band_width
    step = cfg.profit_bank_rate_step

    pos = week_realized_pnl_before
    end = week_realized_pnl_before + gain
    banked = 0.0

    while pos < end:
        if pos < threshold:
            segment_end = min(threshold, end)
            rate = 0.0
        else:
            band_index = int((pos - threshold) // width)
            band_end = threshold + (band_index + 1) * width
            segment_end = min(band_end, end)
            rate = min(1.0, (band_index + 1) * step)
        banked += (segment_end - pos) * rate
        pos = segment_end

    return banked


def max_new_position_value(
    total_equity: float, long_hold_capital: float, cfg: RiskConfig
) -> float:
    cap = cfg.long_hold_capital_cap_pct * total_equity
    utilization = 0.0 if cap <= 0 else min(long_hold_capital / cap, 1.0)
    pct = cfg.max_position_pct - (cfg.max_position_pct - cfg.min_position_pct) * utilization
    return pct * total_equity


def circuit_breaker_tripped(
    month_start_equity: float, current_equity: float, cfg: RiskConfig
) -> bool:
    if month_start_equity <= 0:
        return False
    drawdown = (month_start_equity - current_equity) / month_start_equity
    return drawdown >= cfg.monthly_circuit_breaker_pct


@dataclass
class BuyDecision:
    approved: bool
    reason: str
    max_position_value: float


def evaluate_buy(
    state: PortfolioState,
    symbol: str,
    proposed_value: float,
    total_equity: float,
    cfg: RiskConfig,
    sector: str | None,
    rsi: float,
    ma_trend_bullish: bool | None,
    golden_cross_bullish: bool | None,
) -> BuyDecision:
    max_value = max_new_position_value(total_equity, state.long_hold_capital(), cfg)

    if state.is_held(symbol):
        return BuyDecision(False, "symbol already held", max_value)

    if sector is not None:
        sector_count = sum(1 for p in state.active_positions if p.sector == sector)
        if sector_count >= cfg.max_positions_per_sector:
            return BuyDecision(
                False,
                f"sector concentration: already at the {cfg.max_positions_per_sector}-position limit for {sector}",
                max_value,
            )

    if rsi > cfg.rsi_overbought_threshold:
        return BuyDecision(
            False,
            f"overbought: RSI {rsi:.1f} exceeds {cfg.rsi_overbought_threshold:.0f}",
            max_value,
        )

    if ma_trend_bullish is False:
        return BuyDecision(False, "no confirmed short-term uptrend (short MA at or below long MA)", max_value)

    if golden_cross_bullish is False:
        return BuyDecision(
            False,
            "long-term trend bearish (50-day SMA at or below 200-day SMA / death cross)",
            max_value,
        )

    if circuit_breaker_tripped(state.month_start_equity, total_equity, cfg):
        return BuyDecision(False, "monthly circuit breaker tripped", max_value)

    effective_max_active_positions = cfg.max_active_positions + bonus_active_slots(
        state.prior_week_realized_pnl, cfg
    )
    if state.active_slot_count() >= effective_max_active_positions:
        return BuyDecision(False, "no active slots available", max_value)

    if proposed_value > max_value:
        return BuyDecision(
            False, f"proposed value exceeds max position size of {max_value:.2f}", max_value
        )

    if proposed_value > state.cash:
        return BuyDecision(False, "insufficient cash", max_value)

    return BuyDecision(True, "approved", max_value)


@dataclass
class SellDecision:
    approved: bool
    reason: str


def evaluate_sell(state: PortfolioState, symbol: str) -> SellDecision:
    if state.is_held(symbol):
        return SellDecision(True, "approved")
    return SellDecision(False, "symbol not currently held")
