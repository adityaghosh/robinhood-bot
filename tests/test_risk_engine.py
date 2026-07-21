# tests/test_risk_engine.py
from datetime import date, timedelta

import pytest

from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState
from robinhood_bot.risk_engine import (
    RiskConfig, ExitAction, banked_amount, bonus_active_slots, current_weekly_tier, evaluate_position,
    evaluate_profit_exits, max_new_position_value, circuit_breaker_tripped, evaluate_buy,
    evaluate_sell,
)


def _position(**overrides):
    defaults = dict(
        symbol="AAPL",
        qty=10,
        entry_price=100.0,
        entry_date=date(2026, 7, 1),
        status=PositionStatus.ACTIVE,
        underwater_since=None,
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_small_loss_within_stop_loss_stays_active():
    cfg = RiskConfig(stop_loss_pct=0.05)
    position = _position(entry_price=100.0)
    result = evaluate_position(position, current_price=97.0, today=date(2026, 7, 10), cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.ACTIVE
    assert result.new_underwater_since is None


def test_first_breach_of_stop_loss_enters_waiting():
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)
    position = _position(entry_price=100.0, status=PositionStatus.ACTIVE, underwater_since=None)
    today = date(2026, 7, 10)
    result = evaluate_position(position, current_price=94.0, today=today, cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.WAITING
    assert result.new_underwater_since == today


def test_waiting_within_grace_period_stays_waiting():
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)
    underwater_since = date(2026, 7, 5)
    position = _position(
        entry_price=100.0, status=PositionStatus.WAITING, underwater_since=underwater_since
    )
    today = underwater_since + timedelta(days=5)
    result = evaluate_position(position, current_price=94.0, today=today, cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.WAITING


def test_waiting_past_grace_period_promotes_to_long_hold():
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)
    underwater_since = date(2026, 7, 5)
    position = _position(
        entry_price=100.0, status=PositionStatus.WAITING, underwater_since=underwater_since
    )
    today = underwater_since + timedelta(days=6)
    result = evaluate_position(position, current_price=94.0, today=today, cfg=cfg)
    assert result.action == ExitAction.PROMOTE_LONG_HOLD
    assert result.new_status == PositionStatus.LONG_HOLD


def test_recovery_from_waiting_returns_to_active():
    cfg = RiskConfig(stop_loss_pct=0.05)
    position = _position(
        entry_price=100.0, status=PositionStatus.WAITING, underwater_since=date(2026, 7, 5)
    )
    result = evaluate_position(position, current_price=99.0, today=date(2026, 7, 8), cfg=cfg)
    assert result.action == ExitAction.HOLD
    assert result.new_status == PositionStatus.ACTIVE
    assert result.new_underwater_since is None


def test_max_position_value_at_zero_long_hold_utilization():
    cfg = RiskConfig(max_position_pct=0.20, min_position_pct=0.05, long_hold_capital_cap_pct=0.30)
    value = max_new_position_value(total_equity=10_000.0, long_hold_capital=0.0, cfg=cfg)
    assert value == pytest.approx(2_000.0)


def test_max_position_value_at_full_long_hold_utilization():
    cfg = RiskConfig(max_position_pct=0.20, min_position_pct=0.05, long_hold_capital_cap_pct=0.30)
    value = max_new_position_value(total_equity=10_000.0, long_hold_capital=3_000.0, cfg=cfg)
    assert value == pytest.approx(500.0)


def test_max_position_value_at_half_long_hold_utilization():
    cfg = RiskConfig(max_position_pct=0.20, min_position_pct=0.05, long_hold_capital_cap_pct=0.30)
    value = max_new_position_value(total_equity=10_000.0, long_hold_capital=1_500.0, cfg=cfg)
    assert value == pytest.approx(1_250.0)


def test_max_position_value_zero_equity_returns_zero():
    cfg = RiskConfig()
    value = max_new_position_value(total_equity=0.0, long_hold_capital=0.0, cfg=cfg)
    assert value == pytest.approx(0.0)


def test_circuit_breaker_not_tripped_below_threshold():
    cfg = RiskConfig(monthly_circuit_breaker_pct=0.10)
    assert circuit_breaker_tripped(month_start_equity=10_000.0, current_equity=9_500.0, cfg=cfg) is False


def test_circuit_breaker_tripped_at_threshold():
    cfg = RiskConfig(monthly_circuit_breaker_pct=0.10)
    assert circuit_breaker_tripped(month_start_equity=10_000.0, current_equity=9_000.0, cfg=cfg) is True


def test_circuit_breaker_ignored_when_month_start_equity_zero():
    cfg = RiskConfig()
    assert circuit_breaker_tripped(month_start_equity=0.0, current_equity=9_000.0, cfg=cfg) is False


def test_bonus_active_slots_zero_when_surplus_not_positive():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(500.0, cfg) == 0
    assert bonus_active_slots(0.0, cfg) == 0
    assert bonus_active_slots(-200.0, cfg) == 0


def test_bonus_active_slots_grants_one_slot_at_exact_surplus_boundary():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(1_000.0, cfg) == 1


def test_bonus_active_slots_grants_multiple_slots_for_larger_surplus():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(1_200.0, cfg) == 1
    assert bonus_active_slots(1_700.0, cfg) == 2


def test_bonus_active_slots_caps_at_max_bonus_active_slots():
    cfg = RiskConfig(weekly_profit_goal=500.0, max_bonus_active_slots=2)
    assert bonus_active_slots(5_000.0, cfg) == 2


def test_evaluate_buy_rejects_when_symbol_already_held():
    cfg = RiskConfig()
    state = PortfolioState(cash=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_buy(state, "AAPL", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is False
    assert "already held" in decision.reason


def test_evaluate_buy_rejects_when_circuit_breaker_tripped():
    cfg = RiskConfig(monthly_circuit_breaker_pct=0.10)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=8_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is False
    assert "circuit breaker" in decision.reason


def test_evaluate_buy_rejects_when_no_active_slots():
    cfg = RiskConfig(max_active_positions=1)
    state = PortfolioState(cash=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is False
    assert "slots" in decision.reason


def test_evaluate_buy_rejects_when_oversized():
    cfg = RiskConfig(max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=5_000.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is False
    assert "exceeds max position size" in decision.reason


def test_evaluate_buy_rejects_when_insufficient_cash():
    cfg = RiskConfig(max_position_pct=0.50)
    state = PortfolioState(cash=1_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=2_000.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is False
    assert "insufficient cash" in decision.reason


def test_evaluate_buy_approves_happy_path():
    cfg = RiskConfig(max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is True
    assert decision.max_position_value == 2_000.0


def test_evaluate_buy_rejects_when_sector_concentration_limit_reached():
    cfg = RiskConfig(max_positions_per_sector=1)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
    ])
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg, sector="Technology", rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is False
    assert "sector concentration" in decision.reason


def test_evaluate_buy_approves_when_different_sector_held():
    cfg = RiskConfig(max_positions_per_sector=1, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
    ])
    decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials", rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is True


def test_evaluate_buy_approves_when_sector_none_bypasses_concentration_check():
    cfg = RiskConfig(max_positions_per_sector=1, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0, active_positions=[
        Position("TQQQ", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector=None)
    ])
    decision = evaluate_buy(state, "UPRO", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is True


def test_evaluate_buy_approves_when_bonus_slot_from_prior_week_surplus_allows_it():
    cfg = RiskConfig(
        max_active_positions=1, weekly_profit_goal=500.0, max_bonus_active_slots=2,
        max_position_pct=0.20,
    )
    state = PortfolioState(
        cash=10_000.0, month_start_equity=10_000.0, prior_week_realized_pnl=1_200.0,
        active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
        ],
    )
    decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Financials", rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is True


def test_evaluate_buy_rejects_when_even_boosted_effective_cap_is_reached():
    cfg = RiskConfig(
        max_active_positions=1, weekly_profit_goal=500.0, max_bonus_active_slots=2,
        max_position_pct=0.20,
    )
    state = PortfolioState(
        cash=10_000.0, month_start_equity=10_000.0, prior_week_realized_pnl=1_200.0,
        active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology"),
            Position("MSFT", 5, 300.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Financials"),
        ],
    )
    decision = evaluate_buy(state, "JPM", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg, sector="Energy", rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None)
    assert decision.approved is False
    assert "no active slots available" in decision.reason


def test_evaluate_buy_rejects_when_overbought():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=75.0, ma_trend_bullish=True, golden_cross_bullish=True,
    )
    assert decision.approved is False
    assert "overbought" in decision.reason


def test_evaluate_buy_rejects_when_no_confirmed_uptrend():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=False, golden_cross_bullish=None,
    )
    assert decision.approved is False
    assert "uptrend" in decision.reason


def test_evaluate_buy_approves_when_ma_trend_unknown_bypasses_check():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    )
    assert decision.approved is True


def test_evaluate_buy_approves_at_exact_rsi_threshold():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=70.0, ma_trend_bullish=True, golden_cross_bullish=True,
    )
    assert decision.approved is True


def test_evaluate_buy_rejects_when_death_cross():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=True, golden_cross_bullish=False,
    )
    assert decision.approved is False
    assert "death cross" in decision.reason


def test_evaluate_buy_approves_when_golden_cross_unknown_bypasses_check():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=True, golden_cross_bullish=None,
    )
    assert decision.approved is True


def test_evaluate_buy_approves_when_golden_cross_bullish():
    cfg = RiskConfig(rsi_overbought_threshold=70.0, max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(
        state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg,
        sector=None, rsi=50.0, ma_trend_bullish=True, golden_cross_bullish=True,
    )
    assert decision.approved is True


def test_evaluate_sell_approves_active_holding():
    state = PortfolioState(cash=0.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_sell(state, "AAPL")
    assert decision.approved is True


def test_evaluate_sell_approves_long_hold_holding():
    state = PortfolioState(cash=0.0, long_hold_positions=[
        Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)
    ])
    decision = evaluate_sell(state, "TSLA")
    assert decision.approved is True


def test_evaluate_sell_rejects_unheld_symbol():
    state = PortfolioState(cash=0.0)
    decision = evaluate_sell(state, "NFLX")
    assert decision.approved is False


def test_current_weekly_tier_at_zero_realized():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert current_weekly_tier(0.0, cfg) == 500.0


def test_current_weekly_tier_escalates_past_first_goal():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert current_weekly_tier(520.0, cfg) == 1000.0


def test_current_weekly_tier_handles_negative_realized_pnl():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert current_weekly_tier(-200.0, cfg) == 0.0


def test_current_weekly_tier_clamps_deep_negative_realized_pnl_to_zero():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert current_weekly_tier(-600.0, cfg) == 0.0


def test_banked_amount_zero_below_weekly_profit_goal():
    cfg = RiskConfig(weekly_profit_goal=500.0, profit_bank_band_width=100.0, profit_bank_rate_step=0.25)
    assert banked_amount(week_realized_pnl_before=100.0, gain=200.0, cfg=cfg) == 0.0


def test_banked_amount_zero_for_a_loss_or_breakeven():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    assert banked_amount(week_realized_pnl_before=600.0, gain=-50.0, cfg=cfg) == 0.0
    assert banked_amount(week_realized_pnl_before=600.0, gain=0.0, cfg=cfg) == 0.0


def test_banked_amount_splits_gain_across_bracket_boundary():
    # before=450, gain=200 -> end=650. $50 of the gain (450->500) is below
    # the threshold (0% banked), the next $100 (500->600) is in the first
    # band (25%), and the last $50 (600->650) is in the second band (50%).
    cfg = RiskConfig(weekly_profit_goal=500.0, profit_bank_band_width=100.0, profit_bank_rate_step=0.25)
    banked = banked_amount(week_realized_pnl_before=450.0, gain=200.0, cfg=cfg)
    assert banked == pytest.approx(0.0 + 100.0 * 0.25 + 50.0 * 0.5)


def test_banked_amount_caps_at_full_rate_once_bands_exceed_100_percent():
    # before=850, gain=100 -> entirely within bands 4+ ((850-500)//100=3,
    # rate=min(1,(3+1)*0.25)=1.0), so the whole gain is banked.
    cfg = RiskConfig(weekly_profit_goal=500.0, profit_bank_band_width=100.0, profit_bank_rate_step=0.25)
    banked = banked_amount(week_realized_pnl_before=850.0, gain=100.0, cfg=cfg)
    assert banked == pytest.approx(100.0)


def test_banked_amount_handles_negative_starting_realized_pnl():
    # before=-200 (a loss earlier this week), gain=800 -> end=600. Only the
    # $100 above the $500 threshold (500->600) is banked, at the first
    # band's 25% rate.
    cfg = RiskConfig(weekly_profit_goal=500.0, profit_bank_band_width=100.0, profit_bank_rate_step=0.25)
    banked = banked_amount(week_realized_pnl_before=-200.0, gain=800.0, cfg=cfg)
    assert banked == pytest.approx(25.0)


def test_evaluate_profit_exits_sells_single_winner_reaching_tier():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={"AAPL": 160.0}, week_realized_pnl=0.0, cfg=cfg)
    assert result == [position]


def test_evaluate_profit_exits_sells_biggest_winners_first_until_tier_cleared():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    big = _position(symbol="BIG", qty=10, entry_price=100.0)
    medium = _position(symbol="MED", qty=10, entry_price=100.0)
    small = _position(symbol="SML", qty=10, entry_price=100.0)

    result = evaluate_profit_exits(
        [small, big, medium],
        prices={"BIG": 140.0, "MED": 120.0, "SML": 105.0},
        week_realized_pnl=0.0, cfg=cfg,
    )

    assert result == [big, medium]


def test_evaluate_profit_exits_escalates_tier_when_goal_already_banked():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={"AAPL": 150.0}, week_realized_pnl=520.0, cfg=cfg)
    assert result == [position]


def test_evaluate_profit_exits_sells_nothing_without_positive_gains():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={"AAPL": 95.0}, week_realized_pnl=0.0, cfg=cfg)
    assert result == []


def test_evaluate_profit_exits_skips_candidate_with_missing_quote():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    position = _position(symbol="AAPL", qty=10, entry_price=100.0)
    result = evaluate_profit_exits([position], prices={}, week_realized_pnl=0.0, cfg=cfg)
    assert result == []


def test_evaluate_profit_exits_treats_long_hold_positions_as_eligible():
    cfg = RiskConfig(weekly_profit_goal=500.0)
    long_hold = _position(symbol="TSLA", qty=5, entry_price=200.0, status=PositionStatus.LONG_HOLD)
    result = evaluate_profit_exits([long_hold], prices={"TSLA": 320.0}, week_realized_pnl=0.0, cfg=cfg)
    assert result == [long_hold]
