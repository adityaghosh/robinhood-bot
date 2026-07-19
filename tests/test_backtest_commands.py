import csv
from datetime import date

import pytest

from robinhood_bot import backtest_commands, ledger
from robinhood_bot.backtest_data import HistoricalBar, HistoricalPriceStore
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState
from robinhood_bot.risk_engine import RiskConfig


def test_resolve_run_paths_groups_files_under_run_id(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)

    assert paths.ledger == tmp_path / "run1" / "ledger.json"
    assert paths.trade_log == tmp_path / "run1" / "trade_log.csv"
    assert paths.equity_curve == tmp_path / "run1" / "equity_curve.csv"


def test_cmd_backtest_state_reads_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=5_000.0))

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5),
    )

    assert result["cash"] == 5_000.0
    assert result["trading_mode"] == "backtest"


def test_cmd_backtest_quote_returns_price_from_store(tmp_path):
    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [HistoricalBar(date(2026, 1, 5), 100.0, 101.0, 99.0, 100.5)]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_quote("AAPL", date(2026, 1, 5), store)

    assert result == {"symbol": "AAPL", "date": "2026-01-05", "price": 100.5}


def test_cmd_backtest_quote_returns_none_price_when_missing(tmp_path):
    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return []

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_quote("AAPL", date(2026, 1, 5), store)

    assert result["price"] is None


def test_cmd_backtest_risk_check_uses_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=10_000.0, month_start_equity=10_000.0))
    cfg = RiskConfig(max_position_pct=0.20)

    result = backtest_commands.cmd_backtest_risk_check(
        "run1", tmp_path, starting_cash=0.0, action="buy", symbol="MSFT",
        proposed_value=1_500.0, prices={}, cfg=cfg,
    )

    assert result["approved"] is True


def test_cmd_backtest_record_fill_writes_isolated_trade_log(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=10_000.0))

    result = backtest_commands.cmd_backtest_record_fill(
        "run1", tmp_path, starting_cash=0.0, action="buy", symbol="MSFT",
        qty=5, price=300.0, asof=date(2026, 1, 5), reason="test",
    )

    assert result["cash"] == 8_500.0
    assert paths.trade_log.exists()
    reloaded = ledger.load_state(paths.ledger, starting_cash=0.0)
    assert reloaded.active_positions[0].symbol == "MSFT"


def test_cmd_backtest_check_stop_losses_uses_isolated_ledger(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=0.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 1, 1), PositionStatus.ACTIVE)],
    ))
    cfg = RiskConfig(profit_target_pct=0.08)

    result = backtest_commands.cmd_backtest_check_stop_losses(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 110.0}, asof=date(2026, 1, 10),
        cfg=cfg, apply=True,
    )

    assert result["results"][0]["action"] == "SELL"


def test_cmd_backtest_trading_days_returns_isoformat_dates(tmp_path):
    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                HistoricalBar(date(2026, 1, 5), 402.0, 403.0, 401.0, 402.0),
            ]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_trading_days(date(2026, 1, 1), date(2026, 1, 6), store)

    assert result["trading_days"] == ["2026-01-02", "2026-01-05"]


def test_rank_candidates_as_of_ranks_by_recent_volatility(tmp_path):
    low_bars = [
        HistoricalBar(date(2026, 1, 1), 100.0, 100.5, 99.5, 100.0),
        HistoricalBar(date(2026, 1, 2), 100.0, 100.5, 99.7, 100.1),
        HistoricalBar(date(2026, 1, 3), 100.1, 100.6, 99.8, 100.05),
    ]
    high_bars = [
        HistoricalBar(date(2026, 1, 1), 100.0, 110.0, 90.0, 100.0),
        HistoricalBar(date(2026, 1, 2), 100.0, 115.0, 85.0, 105.0),
        HistoricalBar(date(2026, 1, 3), 105.0, 120.0, 80.0, 95.0),
    ]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            bars = {"LOW": low_bars, "HIGH": high_bars}[symbol]
            return [b for b in bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    ranked = backtest_commands.rank_candidates_as_of(
        ["LOW", "HIGH"], store, date(2026, 1, 3), vol_window_days=2, atr_window_days=2,
    )

    assert ranked == ["HIGH", "LOW"]


def test_rank_candidates_as_of_skips_symbols_with_insufficient_history(tmp_path):
    old_bars = [
        HistoricalBar(date(2026, 1, 1), 100.0, 100.5, 99.5, 100.0),
        HistoricalBar(date(2026, 1, 2), 100.0, 100.5, 99.7, 100.1),
        HistoricalBar(date(2026, 1, 3), 100.1, 100.6, 99.8, 100.05),
    ]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            if symbol == "NEW":
                return [HistoricalBar(date(2026, 1, 3), 50.0, 51.0, 49.0, 50.0)]
            return [b for b in old_bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    ranked = backtest_commands.rank_candidates_as_of(
        ["OLD", "NEW"], store, date(2026, 1, 3), vol_window_days=2, atr_window_days=2,
    )

    assert ranked == ["OLD"]


def test_cmd_backtest_run_executes_deterministic_entry_exit_cycle(tmp_path):
    bars = {
        "A": [
            HistoricalBar(date(2025, 12, 31), 98.0, 99.5, 97.5, 99.0),
            HistoricalBar(date(2026, 1, 2), 99.0, 100.5, 98.5, 100.0),
            HistoricalBar(date(2026, 1, 5), 106.0, 109.0, 105.0, 108.0),
        ],
        "SPY": [
            HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
            HistoricalBar(date(2026, 1, 5), 402.0, 404.0, 401.0, 403.0),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars[symbol] if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.05, profit_target_pct=0.08,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
    )

    result = backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 5),
        candidate_symbols=["A"], store=store, cfg=cfg,
    )

    assert result["trading_days"] == 2

    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    assert final_state.active_positions[0].symbol == "A"
    assert final_state.active_positions[0].qty == 48
    assert final_state.active_positions[0].entry_price == 108.0
    assert final_state.cash == pytest.approx(5_216.0)
    assert final_state.month == "2026-01"
    assert final_state.month_start_equity == pytest.approx(10_000.0)

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert [r["action"] for r in rows] == ["BUY", "SELL", "BUY"]

    with paths.equity_curve.open() as f:
        equity_rows = list(csv.DictReader(f))
    assert equity_rows[0]["date"] == "2026-01-02"
    assert float(equity_rows[0]["total_equity"]) == pytest.approx(10_000.0)
    assert equity_rows[1]["date"] == "2026-01-05"
    assert float(equity_rows[1]["total_equity"]) == pytest.approx(10_400.0)


def test_cmd_backtest_report_computes_return_and_benchmark(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    paths.equity_curve.parent.mkdir(parents=True, exist_ok=True)
    with paths.equity_curve.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "cash", "positions_value", "total_equity"])
        writer.writeheader()
        writer.writerow({"date": "2026-01-02", "cash": 5000.0, "positions_value": 5000.0, "total_equity": 10000.0})
        writer.writerow({"date": "2026-01-05", "cash": 5216.0, "positions_value": 5184.0, "total_equity": 10400.0})
    with paths.trade_log.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "action", "symbol", "qty", "price", "reason"])
        writer.writeheader()
        writer.writerow({"timestamp": "2026-01-02", "action": "BUY", "symbol": "A", "qty": 50, "price": 100.0, "reason": "entry"})
        writer.writerow({"timestamp": "2026-01-05", "action": "SELL", "symbol": "A", "qty": 50, "price": 108.0, "reason": "exit"})

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                HistoricalBar(date(2026, 1, 5), 402.0, 404.0, 401.0, 404.0),
            ]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_report("run1", tmp_path, store)

    assert result["starting_equity"] == 10_000.0
    assert result["ending_equity"] == 10_400.0
    assert result["total_return_pct"] == pytest.approx(0.04)
    assert result["max_drawdown_pct"] == 0.0
    assert result["wins"] == 1
    assert result["losses"] == 0
    assert result["benchmark_return_pct"] == pytest.approx(0.01)


def test_cmd_backtest_report_computes_max_drawdown(tmp_path):
    paths = backtest_commands.resolve_run_paths("run2", tmp_path)
    paths.equity_curve.parent.mkdir(parents=True, exist_ok=True)
    with paths.equity_curve.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "cash", "positions_value", "total_equity"])
        writer.writeheader()
        writer.writerow({"date": "2026-01-02", "cash": 10000.0, "positions_value": 0.0, "total_equity": 10000.0})
        writer.writerow({"date": "2026-01-05", "cash": 9000.0, "positions_value": 0.0, "total_equity": 9000.0})
        writer.writerow({"date": "2026-01-06", "cash": 9500.0, "positions_value": 0.0, "total_equity": 9500.0})

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [
                HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
                HistoricalBar(date(2026, 1, 6), 400.0, 401.0, 399.0, 400.0),
            ]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_report("run2", tmp_path, store)

    assert result["max_drawdown_pct"] == pytest.approx(0.10)
    assert result["wins"] == 0
    assert result["losses"] == 0


def test_cmd_backtest_report_raises_when_no_equity_curve(tmp_path):
    with pytest.raises(ValueError):
        backtest_commands.cmd_backtest_report("missing-run", tmp_path, store=None)
