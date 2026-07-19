import json

from robinhood_bot import cli, universe


def test_cli_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")

    exit_code = cli.main(["state", "--prices-json", "{}"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["cash"] == cli.STARTING_CASH
    assert output["active_positions"] == []


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
