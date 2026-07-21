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


def test_cmd_state_includes_banked_cash_in_output_and_total_equity(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    state = PortfolioState(
        cash=5_000.0,
        banked_cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    )
    ledger.save_state(ledger_path, state)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
    )

    assert result["banked_cash"] == 1_000.0
    assert result["total_equity"] == 7_100.0


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


def test_cmd_state_includes_effective_max_active_positions_with_bonus(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, week="2026-W28", prior_week_realized_pnl=1_200.0))
    cfg = RiskConfig(max_active_positions=5, weekly_profit_goal=500.0, max_bonus_active_slots=2)

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={}, today=date(2026, 7, 10), trading_mode="paper",
        cfg=cfg,
    )

    assert result["prior_week_realized_pnl"] == 1_200.0
    assert result["effective_max_active_positions"] == 6


def test_cmd_state_includes_fresh_rsi_and_ma_trend_for_held_positions(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, rsi=50.0)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
        rsi_by_symbol={"AAPL": 81.3}, ma_trend_by_symbol={"AAPL": False},
    )

    assert result["active_positions"][0]["rsi"] == 81.3
    assert result["active_positions"][0]["ma_trend_bullish"] is False


def test_cmd_state_includes_fresh_golden_cross_for_held_positions(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, rsi=50.0)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
        rsi_by_symbol={"AAPL": 81.3}, ma_trend_by_symbol={"AAPL": False},
        golden_cross_by_symbol={"AAPL": True},
    )

    assert result["active_positions"][0]["golden_cross_bullish"] is True


def test_cmd_state_defaults_rsi_and_ma_trend_when_not_supplied(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
    )

    assert result["active_positions"][0]["rsi"] == 50.0
    assert result["active_positions"][0]["ma_trend_bullish"] is None


def test_cmd_state_defaults_golden_cross_to_none_when_not_supplied(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))

    result = commands.cmd_state(
        ledger_path, starting_cash=0.0, prices={"AAPL": 110.0}, today=date(2026, 7, 10),
        trading_mode="paper", cfg=RiskConfig(),
    )

    assert result["active_positions"][0]["golden_cross_bullish"] is None


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


def test_cmd_risk_check_max_position_value_includes_banked_cash(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=5_000.0, banked_cash=5_000.0, month_start_equity=10_000.0,
    ))
    cfg = RiskConfig(max_position_pct=0.20)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_000.0, prices={}, cfg=cfg,
    )

    # total_equity = 5,000 tradeable + 5,000 banked = 10,000 -> 20% = 2,000,
    # not the 1,000 it would be if banked_cash were excluded from equity.
    assert result["max_position_value"] == 2_000.0


def test_cmd_risk_check_buy_rejects_on_overbought_rsi(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0, month_start_equity=10_000.0))
    cfg = RiskConfig(max_position_pct=0.20, rsi_overbought_threshold=70.0)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={}, cfg=cfg, rsi=80.0,
    )

    assert result["approved"] is False
    assert "overbought" in result["reason"]


def test_cmd_risk_check_buy_rejects_on_sector_concentration(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger.save_state(ledger_path, PortfolioState(
        cash=10_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")],
        month_start_equity=10_000.0,
    ))
    cfg = RiskConfig(max_position_pct=0.20)

    result = commands.cmd_risk_check(
        ledger_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={"AAPL": 100.0}, cfg=cfg, sector="Technology",
    )

    assert result["approved"] is False
    assert "sector concentration" in result["reason"]


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
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle", cfg=RiskConfig(),
    )

    assert result["cash"] == 8_500.0
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.cash == 8_500.0
    assert reloaded.active_positions[0].symbol == "MSFT"
    assert trade_log_path.exists()


def test_cmd_record_fill_buy_persists_sector(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle", cfg=RiskConfig(),
        sector="Technology",
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].sector == "Technology"


def test_cmd_record_fill_buy_persists_rsi_and_ma_trend(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle", cfg=RiskConfig(),
        rsi=62.5, ma_trend_bullish=True,
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].rsi == 62.5
    assert reloaded.active_positions[0].ma_trend_bullish is True


def test_cmd_record_fill_buy_persists_golden_cross(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle", cfg=RiskConfig(),
        rsi=62.5, ma_trend_bullish=True, golden_cross_bullish=True,
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions[0].golden_cross_bullish is True


def test_cmd_record_fill_buy_rejects_insufficient_cash(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=100.0))

    with pytest.raises(ValueError):
        commands.cmd_record_fill(
            ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
            qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle", cfg=RiskConfig(),
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
        qty=10, price=110.0, today=date(2026, 7, 10), reason="profit target", cfg=RiskConfig(),
    )

    assert result["cash"] == 2_100.0
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.active_positions == []


def test_cmd_record_fill_sell_banks_a_portion_of_gain_above_weekly_profit_goal(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(weekly_profit_goal=500.0, profit_bank_band_width=100.0, profit_bank_rate_step=0.25)

    # proceeds = 10*170 = 1,700; gain = (170-100)*10 = 700. $500 of the gain
    # stays reinvestable, the next $100 is banked at 25% ($25), the final
    # $100 at 50% ($50) -- $75 banked, $1,625 of proceeds credited to cash,
    # on top of the starting $1,000 -> $2,625.
    result = commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=170.0, today=date(2026, 7, 10), reason="profit target", cfg=cfg,
    )

    assert result["cash"] == pytest.approx(2_625.0)
    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.cash == pytest.approx(2_625.0)
    assert reloaded.banked_cash == pytest.approx(75.0)
    assert reloaded.week_realized_pnl == pytest.approx(700.0)


def test_cmd_record_fill_sell_at_a_loss_does_not_bank(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)],
        week_realized_pnl=600.0,
    ))
    cfg = RiskConfig(weekly_profit_goal=500.0)

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="AAPL",
        qty=10, price=90.0, today=date(2026, 7, 10), reason="stop loss", cfg=cfg,
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.banked_cash == 0.0
    assert reloaded.cash == pytest.approx(1_900.0)


def test_cmd_record_fill_buy_does_not_bank(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=10_000.0))

    commands.cmd_record_fill(
        ledger_path, trade_log_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, today=date(2026, 7, 10), reason="daily cycle", cfg=RiskConfig(),
    )

    reloaded = ledger.load_state(ledger_path, starting_cash=0.0)
    assert reloaded.banked_cash == 0.0


def test_cmd_record_fill_sell_unheld_symbol_raises(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    trade_log_path = tmp_path / "trade_log.csv"
    ledger.save_state(ledger_path, PortfolioState(cash=1_000.0))

    with pytest.raises(ValueError):
        commands.cmd_record_fill(
            ledger_path, trade_log_path, starting_cash=0.0, action="sell", symbol="NFLX",
            qty=1, price=10.0, today=date(2026, 7, 10), reason="test", cfg=RiskConfig(),
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
            qty=5, price=110.0, today=date(2026, 7, 10), reason="partial sell attempt", cfg=RiskConfig(),
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
        qty=10, price=110.0, today=date(2026, 7, 10), reason="profit target", cfg=RiskConfig(),
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
        qty=10, price=90.0, today=date(2026, 7, 10), reason="stop loss", cfg=RiskConfig(),
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
        qty=10, price=110.0, today=date(2026, 7, 10), reason="test", cfg=RiskConfig(),
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
