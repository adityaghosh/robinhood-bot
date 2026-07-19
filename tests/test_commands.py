from datetime import date

from robinhood_bot import commands, ledger
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState


def test_cmd_state_computes_total_equity_and_pnl(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        month="2026-07",
        month_start_equity=10_000.0,
    )
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10)
    )

    assert result["cash"] == 5_000.0
    assert result["active_positions"][0]["current_value"] == 1_100.0
    assert result["active_positions"][0]["unrealized_pnl_pct"] == 0.1
    assert result["total_equity"] == 6_100.0


def test_cmd_state_marks_missing_price_as_stale(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    )
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10))

    assert result["active_positions"][0]["stale_price"] is True
    assert result["active_positions"][0]["current_value"] == 1_000.0
    assert result["active_positions"][0]["unrealized_pnl_pct"] is None


def test_cmd_state_rolls_month_and_persists(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(cash=10_000.0, month="2026-06", month_start_equity=9_000.0)
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 1))

    assert result["month"] == "2026-07"
    assert result["month_start_equity"] == 10_000.0

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.month == "2026-07"
