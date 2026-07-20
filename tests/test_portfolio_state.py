from datetime import date

from robinhood_bot.portfolio_state import (
    Position, PositionStatus, PortfolioState, roll_month_if_needed, roll_week_if_needed,
)


def test_new_portfolio_has_no_positions():
    state = PortfolioState(cash=10_000.0)
    assert state.active_slot_count() == 0
    assert state.is_held("AAPL") is False
    assert state.long_hold_capital() == 0.0


def test_active_slot_count_reflects_active_positions():
    state = PortfolioState(cash=5_000.0, active_positions=[
        Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE),
        Position("MSFT", 5, 300.0, date(2026, 7, 2), PositionStatus.ACTIVE),
    ])
    assert state.active_slot_count() == 2


def test_find_active_returns_matching_position():
    position = Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    state = PortfolioState(cash=5_000.0, active_positions=[position])
    assert state.find_active("AAPL") is position
    assert state.find_active("MSFT") is None


def test_is_held_checks_both_active_and_long_hold():
    active = Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
    long_hold = Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)
    state = PortfolioState(cash=5_000.0, active_positions=[active], long_hold_positions=[long_hold])
    assert state.is_held("AAPL") is True
    assert state.is_held("TSLA") is True
    assert state.is_held("NFLX") is False


def test_long_hold_capital_sums_cost_basis():
    state = PortfolioState(cash=5_000.0, long_hold_positions=[
        Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD),
        Position("NFLX", 2, 400.0, date(2026, 6, 5), PositionStatus.LONG_HOLD),
    ])
    assert state.long_hold_capital() == 5 * 200.0 + 2 * 400.0


def test_roll_month_if_needed_updates_on_new_month():
    state = PortfolioState(cash=10_000.0, month="2026-06", month_start_equity=9_000.0)
    roll_month_if_needed(state, today=date(2026, 7, 1), current_equity=9_500.0)
    assert state.month == "2026-07"
    assert state.month_start_equity == 9_500.0


def test_roll_month_if_needed_no_change_within_same_month():
    state = PortfolioState(cash=10_000.0, month="2026-07", month_start_equity=9_500.0)
    roll_month_if_needed(state, today=date(2026, 7, 15), current_equity=11_000.0)
    assert state.month == "2026-07"
    assert state.month_start_equity == 9_500.0


def test_roll_week_if_needed_updates_on_new_week():
    state = PortfolioState(cash=10_000.0, week="2026-W01", week_realized_pnl=250.0)
    roll_week_if_needed(state, today=date(2026, 1, 12))
    assert state.week == "2026-W03"
    assert state.week_realized_pnl == 0.0


def test_roll_week_if_needed_no_change_within_same_week():
    state = PortfolioState(cash=10_000.0, week="2026-W03", week_realized_pnl=250.0)
    roll_week_if_needed(state, today=date(2026, 1, 15))
    assert state.week == "2026-W03"
    assert state.week_realized_pnl == 250.0


def test_roll_week_if_needed_handles_iso_year_boundary():
    state = PortfolioState(cash=10_000.0, week="2025-W52", week_realized_pnl=100.0)
    roll_week_if_needed(state, today=date(2025, 12, 29))
    assert state.week == "2026-W01"
    assert state.week_realized_pnl == 0.0


def test_roll_week_if_needed_captures_prior_week_realized_pnl_on_rollover():
    state = PortfolioState(cash=10_000.0, week="2026-W01", week_realized_pnl=700.0)
    roll_week_if_needed(state, today=date(2026, 1, 12))
    assert state.prior_week_realized_pnl == 700.0
    assert state.week_realized_pnl == 0.0


def test_roll_week_if_needed_leaves_prior_week_realized_pnl_untouched_within_same_week():
    state = PortfolioState(
        cash=10_000.0, week="2026-W03", week_realized_pnl=250.0, prior_week_realized_pnl=700.0,
    )
    roll_week_if_needed(state, today=date(2026, 1, 15))
    assert state.prior_week_realized_pnl == 700.0
