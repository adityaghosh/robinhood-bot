# tests/test_risk_engine.py
from datetime import date, timedelta

import pytest

from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState
from robinhood_bot.risk_engine import RiskConfig, ExitAction, evaluate_position, max_new_position_value, circuit_breaker_tripped, evaluate_buy


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


def test_profit_target_hit_triggers_sell():
    cfg = RiskConfig(profit_target_pct=0.08)
    position = _position(entry_price=100.0)
    result = evaluate_position(position, current_price=110.0, today=date(2026, 7, 10), cfg=cfg)
    assert result.action == ExitAction.SELL


def test_small_loss_within_stop_loss_stays_active():
    cfg = RiskConfig(stop_loss_pct=0.05, profit_target_pct=0.08)
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
    cfg = RiskConfig(stop_loss_pct=0.05, profit_target_pct=0.08)
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


def test_evaluate_buy_rejects_when_symbol_already_held():
    cfg = RiskConfig()
    state = PortfolioState(cash=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_buy(state, "AAPL", proposed_value=500.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "already held" in decision.reason


def test_evaluate_buy_rejects_when_circuit_breaker_tripped():
    cfg = RiskConfig(monthly_circuit_breaker_pct=0.10)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=8_000.0, cfg=cfg)
    assert decision.approved is False
    assert "circuit breaker" in decision.reason


def test_evaluate_buy_rejects_when_no_active_slots():
    cfg = RiskConfig(max_active_positions=1)
    state = PortfolioState(cash=10_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    ])
    decision = evaluate_buy(state, "MSFT", proposed_value=500.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "slots" in decision.reason


def test_evaluate_buy_rejects_when_oversized():
    cfg = RiskConfig(max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=5_000.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "exceeds max position size" in decision.reason


def test_evaluate_buy_rejects_when_insufficient_cash():
    cfg = RiskConfig(max_position_pct=0.50)
    state = PortfolioState(cash=1_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=2_000.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is False
    assert "insufficient cash" in decision.reason


def test_evaluate_buy_approves_happy_path():
    cfg = RiskConfig(max_position_pct=0.20)
    state = PortfolioState(cash=10_000.0, month_start_equity=10_000.0)
    decision = evaluate_buy(state, "MSFT", proposed_value=1_500.0, total_equity=10_000.0, cfg=cfg)
    assert decision.approved is True
    assert decision.max_position_value == 2_000.0
