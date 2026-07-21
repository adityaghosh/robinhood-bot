import json
from datetime import date

import pytest

from robinhood_bot import backtest_data, cli, universe


def test_cli_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")

    exit_code = cli.main(["state", "--prices-json", "{}"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["cash"] == cli.STARTING_CASH
    assert output["active_positions"] == []


def test_cli_state_command_includes_trading_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")
    monkeypatch.setattr(cli, "TRADING_MODE", "live")

    exit_code = cli.main(["state", "--prices-json", "{}"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_mode"] == "live"


def test_cli_universe_command_prints_json(monkeypatch, capsys):
    fake_candidates = [
        universe.Candidate(
            "AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0, sector="Technology", rsi=62.0,
            ma_trend_bullish=True, golden_cross_bullish=True,
        ),
    ]

    def fake_build_universe(client, cache_path, sector_cache_path, cfg, today, force_refresh):
        return fake_candidates

    monkeypatch.setattr(cli, "build_universe", fake_build_universe)

    exit_code = cli.main(["universe"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidates"][0]["symbol"] == "AAPL"
    assert output["candidates"][0]["combined_rank"] == 1.0
    assert output["candidates"][0]["sector"] == "Technology"
    assert output["candidates"][0]["rsi"] == 62.0
    assert output["candidates"][0]["ma_trend_bullish"] is True
    assert output["candidates"][0]["golden_cross_bullish"] is True


def test_cli_backtest_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return []

    monkeypatch.setattr(cli, "LiveHistoricalDataFetcher", FakeFetcher)

    exit_code = cli.main(["backtest", "state", "--run", "run1", "--asof", "2026-01-05"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_mode"] == "backtest"
    assert output["cash"] == cli.STARTING_CASH


def test_cli_backtest_quote_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path)

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [backtest_data.HistoricalBar(date(2026, 1, 5), 100.0, 101.0, 99.0, 100.5)]

    monkeypatch.setattr(cli, "LiveHistoricalDataFetcher", FakeFetcher)

    exit_code = cli.main(["backtest", "quote", "AAPL", "--asof", "2026-01-05"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["symbol"] == "AAPL"
    assert output["price"] == 100.5


def test_cli_backtest_trading_days_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path)

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                backtest_data.HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                backtest_data.HistoricalBar(date(2026, 1, 5), 402.0, 403.0, 401.0, 402.0),
            ]

    monkeypatch.setattr(cli, "LiveHistoricalDataFetcher", FakeFetcher)

    exit_code = cli.main(["backtest", "trading-days", "--start", "2026-01-01", "--end", "2026-01-06"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_days"] == ["2026-01-02", "2026-01-05"]


def test_cli_backtest_record_fill_command_writes_isolated_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    exit_code = cli.main([
        "backtest", "record-fill", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--qty", "5", "--price", "300.0", "--reason", "test",
    ])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["cash"] == cli.STARTING_CASH - 1_500.0
    assert (tmp_path / "run1" / "ledger.json").exists()


def test_cli_backtest_mark_day_command_appends_equity_curve_row(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    exit_code = cli.main([
        "backtest", "mark-day", "--run", "run1", "--asof", "2026-01-05", "--prices-json", "{}",
    ])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "date": "2026-01-05", "cash": cli.STARTING_CASH, "banked_cash": 0.0, "positions_value": 0.0,
        "total_equity": cli.STARTING_CASH,
    }
    assert (tmp_path / "run1" / "equity_curve.csv").exists()


def test_cli_backtest_run_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    fake_candidates = [universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0, sector="Technology")]
    monkeypatch.setattr(
        cli, "build_universe", lambda client, cache_path, sector_cache_path, cfg, today: fake_candidates
    )

    captured = {}

    def fake_cmd_backtest_run(
        run_id, base_dir, starting_cash, start, end, candidate_symbols, candidate_sectors, store, cfg,
        benchmark_symbol,
    ):
        captured["candidate_symbols"] = candidate_symbols
        captured["candidate_sectors"] = candidate_sectors
        return {"run_id": run_id, "trading_days": 0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_run", fake_cmd_backtest_run)

    exit_code = cli.main(["backtest", "run", "--run", "run1", "--start", "2026-01-01", "--end", "2026-01-05"])

    assert exit_code == 0
    assert captured["candidate_symbols"] == ["AAPL"]
    assert captured["candidate_sectors"] == {"AAPL": "Technology"}
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run1"


def test_cli_backtest_report_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    def fake_cmd_backtest_report(run_id, base_dir, store, benchmark_symbol):
        return {"run_id": run_id, "total_return_pct": 0.05}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_report", fake_cmd_backtest_report)

    exit_code = cli.main(["backtest", "report", "--run", "run1"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["total_return_pct"] == 0.05


def test_cli_risk_check_buy_passes_sector_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["sector"] = sector
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main([
        "risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}", "--sector", "Technology",
    ])

    assert exit_code == 0
    assert captured["sector"] == "Technology"


def test_cli_backtest_risk_check_passes_sector_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    captured = {}

    def fake_cmd_backtest_risk_check(
        run_id, base_dir, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["sector"] = sector
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_risk_check", fake_cmd_backtest_risk_check)

    exit_code = cli.main([
        "backtest", "risk-check", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--value", "500", "--prices-json", "{}", "--sector", "Financials",
    ])

    assert exit_code == 0
    assert captured["sector"] == "Financials"


def test_cli_risk_check_buy_passes_rsi_and_ma_bullish_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["rsi"] = rsi
        captured["ma_trend_bullish"] = ma_trend_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main([
        "risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}",
        "--rsi", "62.5", "--ma-bullish",
    ])

    assert exit_code == 0
    assert captured["rsi"] == 62.5
    assert captured["ma_trend_bullish"] is True


def test_cli_risk_check_buy_defaults_ma_bullish_to_none_when_omitted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["ma_trend_bullish"] = ma_trend_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main(["risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}"])

    assert exit_code == 0
    assert captured["ma_trend_bullish"] is None


def test_cli_risk_check_buy_passes_golden_cross_bullish_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["golden_cross_bullish"] = golden_cross_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main([
        "risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}",
        "--golden-cross-bullish",
    ])

    assert exit_code == 0
    assert captured["golden_cross_bullish"] is True


def test_cli_risk_check_buy_defaults_golden_cross_bullish_to_none_when_omitted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")

    captured = {}

    def fake_cmd_risk_check(
        ledger_path, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["golden_cross_bullish"] = golden_cross_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.commands, "cmd_risk_check", fake_cmd_risk_check)

    exit_code = cli.main(["risk-check", "buy", "MSFT", "--value", "500", "--prices-json", "{}"])

    assert exit_code == 0
    assert captured["golden_cross_bullish"] is None


def test_cli_backtest_risk_check_passes_rsi_and_ma_bullish_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    captured = {}

    def fake_cmd_backtest_risk_check(
        run_id, base_dir, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["rsi"] = rsi
        captured["ma_trend_bullish"] = ma_trend_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_risk_check", fake_cmd_backtest_risk_check)

    exit_code = cli.main([
        "backtest", "risk-check", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--value", "500", "--prices-json", "{}", "--rsi", "45.0", "--no-ma-bullish",
    ])

    assert exit_code == 0
    assert captured["rsi"] == 45.0
    assert captured["ma_trend_bullish"] is False


def test_cli_backtest_risk_check_passes_golden_cross_bullish_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

    captured = {}

    def fake_cmd_backtest_risk_check(
        run_id, base_dir, starting_cash, action, symbol, proposed_value, prices, cfg,
        sector=None, rsi=50.0, ma_trend_bullish=None, golden_cross_bullish=None,
    ):
        captured["golden_cross_bullish"] = golden_cross_bullish
        return {"approved": True, "reason": "approved", "max_position_value": 0.0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_risk_check", fake_cmd_backtest_risk_check)

    exit_code = cli.main([
        "backtest", "risk-check", "buy", "MSFT", "--run", "run1", "--asof", "2026-01-05",
        "--value", "500", "--prices-json", "{}", "--no-golden-cross-bullish",
    ])

    assert exit_code == 0
    assert captured["golden_cross_bullish"] is False


def test_cli_state_command_fetches_indicators_for_held_positions(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")

    from robinhood_bot import ledger as ledger_module
    from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState

    ledger_module.save_state(
        tmp_path / "ledger.json",
        PortfolioState(cash=5_000.0, active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
        ]),
    )

    class FakeClient:
        def fetch_daily_bars(self, ticker, lookback_days):
            from robinhood_bot.universe import Bar
            return [Bar(101.0 + i, 99.0 + i, 100.0 + i) for i in range(25)]

    monkeypatch.setattr(cli, "LiveMarketDataClient", FakeClient)

    exit_code = cli.main(["state", "--prices-json", '{"AAPL": 124.0}'])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["active_positions"][0]["rsi"] == pytest.approx(100.0)
    assert output["active_positions"][0]["ma_trend_bullish"] is True


def test_cli_state_command_fetches_golden_cross_for_held_positions(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")

    from robinhood_bot import ledger as ledger_module
    from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState

    ledger_module.save_state(
        tmp_path / "ledger.json",
        PortfolioState(cash=5_000.0, active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE)
        ]),
    )

    class FakeClient:
        def fetch_daily_bars(self, ticker, lookback_days):
            from robinhood_bot.universe import Bar
            return [Bar(101.0 + i * 0.1, 99.0 + i * 0.1, 100.0 + i * 0.1) for i in range(201)]

    monkeypatch.setattr(cli, "LiveMarketDataClient", FakeClient)

    exit_code = cli.main(["state", "--prices-json", '{"AAPL": 124.0}'])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["active_positions"][0]["golden_cross_bullish"] is True
