import json
from datetime import date

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
        universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0),
    ]

    def fake_build_universe(client, cache_path, cfg, today, force_refresh):
        return fake_candidates

    monkeypatch.setattr(cli, "build_universe", fake_build_universe)

    exit_code = cli.main(["universe"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidates"][0]["symbol"] == "AAPL"
    assert output["candidates"][0]["combined_rank"] == 1.0


def test_cli_backtest_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path)

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
        "date": "2026-01-05", "cash": cli.STARTING_CASH, "positions_value": 0.0,
        "total_equity": cli.STARTING_CASH,
    }
    assert (tmp_path / "run1" / "equity_curve.csv").exists()


def test_cli_backtest_run_command_delegates_to_backtest_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "HISTORICAL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cli, "BACKTEST_BASE_DIR", tmp_path / "backtests")

    fake_candidates = [universe.Candidate("AAPL", "sp500", 3.0e12, 0.25, 0.02, 1.0)]
    monkeypatch.setattr(cli, "build_universe", lambda client, cache_path, cfg, today: fake_candidates)

    captured = {}

    def fake_cmd_backtest_run(run_id, base_dir, starting_cash, start, end, candidate_symbols, store, cfg, benchmark_symbol):
        captured["candidate_symbols"] = candidate_symbols
        return {"run_id": run_id, "trading_days": 0}

    monkeypatch.setattr(cli.backtest_commands, "cmd_backtest_run", fake_cmd_backtest_run)

    exit_code = cli.main(["backtest", "run", "--run", "run1", "--start", "2026-01-01", "--end", "2026-01-05"])

    assert exit_code == 0
    assert captured["candidate_symbols"] == ["AAPL"]
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
