from datetime import date

import pytest

from robinhood_bot import commands, ledger
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState
from robinhood_bot.risk_engine import RiskConfig


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
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
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

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper",
        cfg=RiskConfig(),
    )

    assert result["active_positions"][0]["stale_price"] is True
    assert result["active_positions"][0]["current_value"] == 1_000.0
    assert result["active_positions"][0]["unrealized_pnl_pct"] is None


def test_cmd_state_rolls_month_and_persists(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(cash=10_000.0, month="2026-06", month_start_equity=9_000.0)
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 1), trading_mode="paper",
        cfg=RiskConfig(),
    )

    assert result["month"] == "2026-07"
    assert result["month_start_equity"] == 10_000.0

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.month == "2026-07"


def test_cmd_state_includes_trading_mode(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=1_000.0))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="live",
        cfg=RiskConfig(),
    )

    assert result["trading_mode"] == "live"


def test_cmd_risk_check_buy_approves_happy_path(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, month_start_equity=10_000.0))
    cfg = RiskConfig(max_position_pct=0.20)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={}, cfg=cfg,
    )

    assert result["approved"] is True
    assert result["max_position_value"] == 2_000.0


def test_cmd_risk_check_buy_rejects_when_slots_full(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=10_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        month_start_equity=10_000.0,
    ))
    cfg = RiskConfig(max_active_positions=1)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=500.0, prices={"AAPL": 100.0}, cfg=cfg,
    )

    assert result["approved"] is False


def test_cmd_risk_check_sell_rejects_unheld_symbol(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))
    cfg = RiskConfig()

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="sell", symbol="NFLX",
        proposed_value=0.0, prices={}, cfg=cfg,
    )

    assert result["approved"] is False


def test_cmd_risk_check_unknown_action_raises(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))
    cfg = RiskConfig()

    with pytest.raises(ValueError):
        commands.cmd_risk_check(
            ledger_path, starting_cash=0.0, action="hold", symbol="AAPL",
            proposed_value=0.0, prices={}, cfg=cfg,
        )


def test_cmd_record_fill_buy_updates_cash_and_adds_position(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    result = commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle",
    )

    assert result["cash"] == 8_500.0
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.cash == 8_500.0
    assert reloaded.active_positions[0].symbol == "MSFT"
    assert trade_log_path.exists()


def test_cmd_record_fill_buy_rejects_insufficient_cash(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=100.0))

    with pytest.raises(ValueError):
        commands.cmd_record_fill(
            ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
            qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle",
        )


def test_cmd_record_fill_sell_removes_position_and_credits_cash(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))

    result = commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=110.0, today=date(2026, 7, 10), reason="profit target",
    )

    assert result["cash"] == 2_100.0
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions == []


def test_cmd_record_fill_sell_unheld_symbol_raises(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=1_000.0))

    with pytest.raises(ValueError):
        commands.cmd_record_fill(
            ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="NFLX",
            qty=1, price=10.0, today=date(2026, 7, 10), reason="test",
        )


def test_cmd_record_fill_sell_qty_mismatch_raises(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))

    with pytest.raises(ValueError):
        commands.cmd_record_fill(
            ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
            qty=5, price=110.0, today=date(2026, 7, 10), reason="partial sell attempt",
        )


def test_check_stop_losses_skips_symbol_without_fresh_price(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig()

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), cfg=cfg, apply=True,
    )

    assert result["results"][0]["action"] == "SKIP"
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].status == PositionStatus.ACTIVE


def test_check_stop_losses_reports_profit_exit_without_removing_position(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(weekly_profit_goal=500.0)

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"AAPL": 160.0}, today=date(2026, 7, 10), cfg=cfg, apply=True,
    )

    sell_results = [r for r in result["results"] if r["action"] == "SELL"]
    assert sell_results == [
        {"symbol": "AAPL", "action": "SELL", "current_status": "ACTIVE", "new_status": "ACTIVE"}
    ]
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].symbol == "AAPL"


def test_check_stop_losses_reports_profit_exit_for_recovered_long_hold_position(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        long_hold_positions=[Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)],
    ))
    cfg = RiskConfig(weekly_profit_goal=500.0)

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"TSLA": 320.0}, today=date(2026, 7, 10), cfg=cfg, apply=True,
    )

    sell_results = [r for r in result["results"] if r["action"] == "SELL"]
    assert sell_results == [
        {"symbol": "TSLA", "action": "SELL", "current_status": "LONG_HOLD", "new_status": "LONG_HOLD"}
    ]


def test_check_stop_losses_promotes_expired_position_to_long_hold(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position(
            "AAPL", 10, 100.0, date(2026, 6, 1), PositionStatus.WAITING,
            underwater_since=date(2026, 7, 1),
        )],
    ))
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)

    result = commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"AAPL": 90.0}, today=date(2026, 7, 8), cfg=cfg, apply=True,
    )

    assert result["results"][0]["action"] == "PROMOTE_LONG_HOLD"
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions == []
    assert reloaded.long_hold_positions[0].symbol == "AAPL"
    assert reloaded.long_hold_positions[0].status == PositionStatus.LONG_HOLD


def test_check_stop_losses_dry_run_does_not_save(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(stop_loss_pct=0.05, grace_period_days=5)

    commands.cmd_check_stop_losses(
        ledger_path, starting_cash=0.0, prices={"AAPL": 90.0}, today=date(2026, 7, 8), cfg=cfg, apply=False,
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].status == PositionStatus.ACTIVE
    assert reloaded.active_positions[0].underwater_since is None


def test_cmd_record_fill_sell_accumulates_week_realized_pnl(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        week="2026-W28", week_realized_pnl=50.0,
    ))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=110.0, today=date(2026, 7, 10), reason="profit target",
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.week_realized_pnl == pytest.approx(150.0)


def test_cmd_record_fill_sell_at_a_loss_decreases_week_realized_pnl(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        week="2026-W28", week_realized_pnl=200.0,
    ))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=90.0, today=date(2026, 7, 10), reason="stop loss",
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.week_realized_pnl == pytest.approx(100.0)


def test_cmd_record_fill_rolls_week_before_accumulating(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        week="2026-W27", week_realized_pnl=999.0,
    ))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=110.0, today=date(2026, 7, 10), reason="test",
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.week == "2026-W28"
    assert reloaded.week_realized_pnl == pytest.approx(100.0)


def test_cmd_state_includes_week_tracking_fields(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, week="2026-W28", week_realized_pnl=250.0))
    cfg = RiskConfig(weekly_profit_goal=500.0)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper", cfg=cfg,
    )

    assert result["week"] == "2026-W28"
    assert result["week_realized_pnl"] == 250.0
    assert result["week_profit_target"] == 500.0


def test_cmd_state_rolls_week_and_persists(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, week="2026-W27", week_realized_pnl=250.0))
    cfg = RiskConfig()

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper", cfg=cfg,
    )

    assert result["week"] == "2026-W28"
    assert result["week_realized_pnl"] == 0.0

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.week == "2026-W28"
