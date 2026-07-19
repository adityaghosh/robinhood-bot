from datetime import date

from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState


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
