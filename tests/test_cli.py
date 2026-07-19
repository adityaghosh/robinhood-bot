import json

from robinhood_bot import cli


def test_cli_state_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(cli, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")

    exit_code = cli.main(["state", "--prices-json", "{}"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["cash"] == cli.STARTING_CASH
    assert output["active_positions"] == []
