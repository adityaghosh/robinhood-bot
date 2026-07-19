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
