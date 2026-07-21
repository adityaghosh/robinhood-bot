import csv
from datetime import date, timedelta

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

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return []

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5), cfg=RiskConfig(), store=store,
    )

    assert result["cash"] == 5_000.0
    assert result["trading_mode"] == "backtest"


def test_cmd_backtest_state_includes_fresh_rsi_for_held_position(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 1, 1), PositionStatus.ACTIVE)],
    ))

    bars = [HistoricalBar(date(2026, 1, 1) + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(25)]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 124.0}, asof=date(2026, 1, 25),
        cfg=RiskConfig(), store=store,
    )

    assert result["active_positions"][0]["rsi"] == pytest.approx(100.0)
    assert result["active_positions"][0]["ma_trend_bullish"] is True


def test_cmd_backtest_state_includes_fresh_golden_cross_for_held_position(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=5_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2025, 6, 1), PositionStatus.ACTIVE)],
    ))

    bars = [
        HistoricalBar(date(2025, 6, 1) + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i)
        for i in range(201)
    ]

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    asof = bars[-1].date

    result = backtest_commands.cmd_backtest_state(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 300.0}, asof=asof,
        cfg=RiskConfig(), store=store,
    )

    assert result["active_positions"][0]["golden_cross_bullish"] is True


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
        qty=5, price=300.0, asof=date(2026, 1, 5), reason="test", cfg=RiskConfig(),
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
    cfg = RiskConfig()

    result = backtest_commands.cmd_backtest_check_stop_losses(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 110.0}, asof=date(2026, 1, 10),
        cfg=cfg, apply=True,
    )

    assert result["results"][-1]["action"] == "SELL"


def test_cmd_backtest_mark_day_appends_equity_curve_row(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=1_000.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 1, 1), PositionStatus.ACTIVE)],
    ))

    row = backtest_commands.cmd_backtest_mark_day(
        "run1", tmp_path, starting_cash=0.0, prices={"AAPL": 110.0}, asof=date(2026, 1, 5),
    )

    assert row == {
        "date": "2026-01-05", "cash": 1_000.0, "banked_cash": 0.0, "positions_value": 1_100.0,
        "total_equity": 2_100.0,
    }
    with paths.equity_curve.open() as f:
        rows = list(csv.DictReader(f))
    assert rows == [{
        "date": "2026-01-05", "cash": "1000.0", "banked_cash": "0.0", "positions_value": "1100.0",
        "total_equity": "2100.0",
    }]


def test_cmd_backtest_mark_day_falls_back_to_entry_price_when_quote_missing(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=500.0,
        active_positions=[Position("AAPL", 10, 100.0, date(2026, 1, 1), PositionStatus.ACTIVE)],
    ))

    row = backtest_commands.cmd_backtest_mark_day(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5),
    )

    assert row["positions_value"] == 1_000.0
    assert row["total_equity"] == 1_500.0


def test_cmd_backtest_mark_day_appends_one_row_per_call(tmp_path):
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(cash=1_000.0))

    backtest_commands.cmd_backtest_mark_day(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 5),
    )
    backtest_commands.cmd_backtest_mark_day(
        "run1", tmp_path, starting_cash=0.0, prices={}, asof=date(2026, 1, 6),
    )

    with paths.equity_curve.open() as f:
        rows = list(csv.DictReader(f))
    assert [r["date"] for r in rows] == ["2026-01-05", "2026-01-06"]


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
        def __init__(self):
            self.calls = []

        def fetch_history(self, symbol, start, end):
            self.calls.append((symbol, start, end))
            return [b for b in bars[symbol] if start <= b.date <= end]

    fetcher = FakeFetcher()
    store = HistoricalPriceStore(fetcher, tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.05, weekly_profit_goal=200.0,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
    )

    result = backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 5),
        candidate_symbols=["A"], candidate_sectors={}, store=store, cfg=cfg,
    )

    assert result["trading_days"] == 2

    # The one-time prefetch pass before the day-by-day loop should mean each
    # candidate symbol needs at most one `fetch_history` call for the whole
    # multi-day run, even though `rank_candidates_as_of` queries it once per
    # day inside the loop.
    assert len([c for c in fetcher.calls if c[0] == "A"]) <= 1

    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    assert final_state.active_positions[0].symbol == "A"
    assert final_state.active_positions[0].qty == 48
    assert final_state.active_positions[0].entry_price == 108.0
    # weekly_profit_goal=200.0 here means the $400 realized gain crosses the
    # profit-banking threshold too: $200 of it stays fully reinvestable (0%
    # banked), the next $100 is banked at 25% ($25), and the final $100 at 50%
    # ($50) -- $75 banked total, so cash reflects proceeds net of that $75,
    # not the full unbanked gain.
    assert final_state.cash == pytest.approx(5_141.0)
    assert final_state.banked_cash == pytest.approx(75.0)
    assert final_state.month == "2026-01"
    assert final_state.month_start_equity == pytest.approx(10_000.0)
    assert final_state.week == "2026-W02"
    assert final_state.week_realized_pnl == pytest.approx(400.0)

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert [r["action"] for r in rows] == ["BUY", "SELL", "BUY"]

    with paths.equity_curve.open() as f:
        equity_rows = list(csv.DictReader(f))
    assert equity_rows[0]["date"] == "2026-01-02"
    assert float(equity_rows[0]["total_equity"]) == pytest.approx(10_000.0)
    assert equity_rows[1]["date"] == "2026-01-05"
    assert float(equity_rows[1]["total_equity"]) == pytest.approx(10_400.0)


def test_cmd_backtest_run_escalates_tier_across_days_in_same_week(tmp_path):
    bars = {
        "A": [
            HistoricalBar(date(2025, 12, 31), 98.0, 99.5, 97.5, 99.0),
            HistoricalBar(date(2026, 1, 2), 99.0, 100.5, 98.5, 100.0),
            HistoricalBar(date(2026, 1, 5), 106.0, 109.0, 105.0, 108.0),
            HistoricalBar(date(2026, 1, 6), 109.0, 111.0, 107.0, 110.0),
        ],
        "SPY": [
            HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0),
            HistoricalBar(date(2026, 1, 5), 402.0, 404.0, 401.0, 403.0),
            HistoricalBar(date(2026, 1, 6), 403.0, 405.0, 402.0, 404.0),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars[symbol] if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.05, weekly_profit_goal=200.0,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
    )

    backtest_commands.cmd_backtest_run(
        "run_escalation", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 1), end=date(2026, 1, 6),
        candidate_symbols=["A"], candidate_sectors={}, store=store, cfg=cfg,
    )

    paths = backtest_commands.resolve_run_paths("run_escalation", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)

    # Day 1: buy 50 A @ $100 (cash 5000). Day 2 (new ISO week, week_realized_pnl
    # resets to 0): A's $400 gain clears the $200 tier -> sold, week_realized_pnl=400;
    # rebuy 48 @ $108 (cash 5216). Day 3 (same week as day 2, no reset): tier has
    # escalated to 600 (the next $200 multiple above 400) -- if escalation were
    # broken and the tier stayed frozen at $200, `running=400 >= tier=200` would
    # break the loop immediately and A would NEVER be sold this day, since day 3's
    # gain (400-108... i.e. (110-108)*48=96) doesn't matter in that broken case.
    # With correct escalation, running(400) < tier(600), so A DOES get sold despite
    # its modest $96 gain, proving the tier genuinely moved from 200 to 600, not
    # just "some sell happened."
    assert final_state.week_realized_pnl == pytest.approx(496.0)
    assert final_state.active_positions[0].symbol == "A"
    assert final_state.active_positions[0].qty == 47
    assert final_state.active_positions[0].entry_price == 110.0
    # weekly_profit_goal=200.0 is also the profit-banking threshold. Day 2's
    # $400 gain banks $75 (same $200/$100/$100 bracket math as the
    # deterministic entry/exit test above). Day 3's $96 gain starts from a
    # week_realized_pnl of 400, which is already two $100-bands past the
    # $200 threshold, landing entirely in the third band (rate 75%): banked
    # = 96 * 0.75 = $72. Total banked across both sells: 75 + 72 = $147.
    assert final_state.cash == pytest.approx(5_179.0)
    assert final_state.banked_cash == pytest.approx(147.0)

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert [r["action"] for r in rows] == ["BUY", "SELL", "BUY", "SELL", "BUY"]


def test_cmd_backtest_run_promotes_expired_underwater_position_to_long_hold(tmp_path):
    # A has been underwater (price 80 vs entry 100, well past stop_loss_pct)
    # since 2025-12-20, already flagged WAITING by a prior day's evaluation.
    # By 2026-01-02 that's 13 days underwater, past grace_period_days=5, so
    # `evaluate_position` should return PROMOTE_LONG_HOLD rather than SELL —
    # this exercises the one exits-phase branch the existing tests skip.
    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=9_000.0,
        active_positions=[Position(
            "A", 10, 100.0, date(2025, 12, 1), PositionStatus.WAITING,
            underwater_since=date(2025, 12, 20),
        )],
        month="2026-01",
        month_start_equity=10_000.0,
    ))

    bars = {
        "SPY": [HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0)],
        "A": [HistoricalBar(date(2026, 1, 2), 82.0, 83.0, 79.0, 80.0)],
        "B": [
            HistoricalBar(date(2025, 12, 30), 47.0, 48.5, 46.5, 48.0),
            HistoricalBar(date(2025, 12, 31), 48.0, 49.5, 47.5, 49.0),
            HistoricalBar(date(2026, 1, 2), 49.0, 50.5, 48.5, 50.0),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars[symbol] if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.05,
        grace_period_days=5, max_position_pct=1.0, min_position_pct=1.0,
    )

    backtest_commands.cmd_backtest_run(
        "run1", tmp_path, starting_cash=9_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=["B"], candidate_sectors={}, store=store, cfg=cfg, vol_window_days=2, atr_window_days=2,
    )

    paths = backtest_commands.resolve_run_paths("run1", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=9_000.0)

    assert [p.symbol for p in final_state.active_positions] != ["A"]
    assert final_state.long_hold_positions[0].symbol == "A"
    assert final_state.long_hold_positions[0].status == PositionStatus.LONG_HOLD
    # The promoted position no longer counts against the active-slot cap, so
    # the freed slot could be (and here, is) filled by the next top candidate.
    assert final_state.active_slot_count() == 1
    assert final_state.active_positions[0].symbol == "B"


def test_cmd_backtest_run_sweeps_recovered_long_hold_position_for_profit(tmp_path):
    bars = {
        "A": [HistoricalBar(date(2026, 1, 2), 148.0, 151.0, 147.0, 150.0)],
        "SPY": [HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0)],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars[symbol] if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(max_active_positions=0, weekly_profit_goal=300.0)

    paths = backtest_commands.resolve_run_paths("run2", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=1_000.0,
        long_hold_positions=[Position("A", 10, 100.0, date(2025, 12, 1), PositionStatus.LONG_HOLD)],
    ))

    backtest_commands.cmd_backtest_run(
        "run2", tmp_path, starting_cash=1_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=[], candidate_sectors={}, store=store, cfg=cfg,
    )

    final_state = ledger.load_state(paths.ledger, starting_cash=1_000.0)
    assert final_state.long_hold_positions == []
    # weekly_profit_goal=300.0 is also the profit-banking threshold: $300 of
    # the $500 gain is fully reinvestable (0% banked), the next $100 is
    # banked at 25% ($25), and the final $100 at 50% ($50) -- $75 banked.
    assert final_state.cash == pytest.approx(2_425.0)
    assert final_state.banked_cash == pytest.approx(75.0)
    assert final_state.week_realized_pnl == pytest.approx(500.0)

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["action"] == "SELL"
    assert rows[0]["symbol"] == "A"
    assert rows[0]["reason"] == "weekly profit-goal exit"


def test_cmd_backtest_run_skips_same_sector_candidate_for_next_ranked(tmp_path):
    # MSFT is already held (sector "Technology"), one free active slot. Of the
    # two candidates, AAPL2 ranks first (far higher volatility/ATR from its
    # wild swings below) but shares MSFT's sector, so it must be REJECTED by
    # the sector-concentration check; JPM ranks second but has a different
    # sector, so it's the one that should actually get bought. If the sector
    # check weren't wired into this loop, AAPL2 (the top-ranked candidate)
    # would be bought instead.
    bars = {
        "SPY": [HistoricalBar(date(2026, 1, 2), 400.0, 401.0, 399.0, 400.0)],
        "AAPL2": [
            HistoricalBar(date(2025, 12, 30), 90.0, 110.0, 80.0, 100.0),
            HistoricalBar(date(2025, 12, 31), 100.0, 130.0, 70.0, 90.0),
            HistoricalBar(date(2026, 1, 2), 90.0, 120.0, 60.0, 110.0),
        ],
        "JPM": [
            HistoricalBar(date(2025, 12, 30), 149.0, 150.5, 148.5, 150.0),
            HistoricalBar(date(2025, 12, 31), 150.0, 151.0, 149.5, 150.2),
            HistoricalBar(date(2026, 1, 2), 150.2, 151.2, 149.8, 150.4),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            # MSFT (the already-held position) is intentionally NOT a key in
            # `bars` -- it isn't a candidate this run needs price history
            # for beyond the exits/equity phases, which already tolerate a
            # missing quote by falling back to entry_price. Use `.get` (not
            # `bars[symbol]`) so that fallback path doesn't KeyError instead.
            return [b for b in bars.get(symbol, []) if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=2, stop_loss_pct=0.5, weekly_profit_goal=100_000.0,
        max_position_pct=1.0, min_position_pct=1.0, grace_period_days=5,
        max_positions_per_sector=1,
    )

    paths = backtest_commands.resolve_run_paths("run_sector", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=10_000.0,
        active_positions=[Position("MSFT", 5, 300.0, date(2025, 12, 1), PositionStatus.ACTIVE, sector="Technology")],
        month="2026-01",
        month_start_equity=11_500.0,
    ))

    backtest_commands.cmd_backtest_run(
        "run_sector", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 2), end=date(2026, 1, 2),
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={"AAPL2": "Technology", "JPM": "Financials"},
        store=store, cfg=cfg, vol_window_days=2, atr_window_days=2,
    )

    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    symbols = {p.symbol for p in final_state.active_positions}
    assert symbols == {"MSFT", "JPM"}
    jpm = next(p for p in final_state.active_positions if p.symbol == "JPM")
    assert jpm.sector == "Financials"
    assert jpm.qty == 66

    with paths.trade_log.open() as f:
        rows = list(csv.DictReader(f))
    assert [r["symbol"] for r in rows] == ["JPM"]


def test_cmd_backtest_run_rejects_overbought_candidate_for_next_ranked(tmp_path):
    # AAPL2 and JPM land in an exact 0.5/0.5 combined vol+ATR score tie
    # (AAPL2's steady +1/day rise gives it a much higher ATR score but a much
    # LOWER realized-vol score than JPM's noisy flat bars -- they average out
    # to the same combined rank), broken by candidate-list order so AAPL2
    # ranks first. AAPL2's monotonic rise also gives it an RSI of 100 --
    # deeply overbought -- so it must be REJECTED by the new RSI gate; JPM
    # (neutral RSI ~50) should be the one actually bought instead. If the RSI
    # gate weren't wired into this loop, AAPL2 (the first-ranked candidate)
    # would be bought instead.
    aapl2_bars = [
        HistoricalBar(date(2025, 12, 15) + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i)
        for i in range(37)
    ]
    jpm_bars = [
        HistoricalBar(date(2025, 12, 15) + timedelta(days=i), 150.0, 150.5, 149.5, 150.0 + (0.1 if i % 2 == 0 else -0.1))
        for i in range(37)
    ]
    bars = {
        "SPY": [HistoricalBar(date(2026, 1, 20), 400.0, 401.0, 399.0, 400.0)],
        "AAPL2": aapl2_bars,
        "JPM": jpm_bars,
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars.get(symbol, []) if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.5, weekly_profit_goal=100_000.0,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
        rsi_overbought_threshold=70.0,
    )

    backtest_commands.cmd_backtest_run(
        "run_rsi", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 20), end=date(2026, 1, 20),
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={}, store=store, cfg=cfg,
        vol_window_days=2, atr_window_days=2,
    )

    paths = backtest_commands.resolve_run_paths("run_rsi", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    assert [p.symbol for p in final_state.active_positions] == ["JPM"]


def test_cmd_backtest_run_rejects_death_cross_candidate_for_next_ranked(tmp_path):
    # AAPL2 declines steadily for 200 days (500.0 down by 1.5/day to 201.5),
    # then has a choppy-but-net-rising 50-day tail (net +0.4/day drift with
    # alternating +0.5/-0.9 noise). That tail keeps its 14-day RSI at ~64.3
    # (not overbought) and its 5-day SMA above its 20-day SMA (confirmed
    # short-term uptrend, not rejected by the existing MA-trend check) --
    # but its 50-day SMA (~164, entirely within the mild recovery) is still
    # far below its 200-day SMA (~267, dominated by the steep decline), so
    # it must be REJECTED by the new golden-cross gate specifically.
    #
    # JPM drifts gently upward the whole time (+0.01/day) with a small
    # alternating +/-0.02 wobble -- enough real up/down movement to keep its
    # 14-day RSI at ~62.5 (not overbought) rather than pinned to 100 by a
    # purely monotonic series, while its volatility/ATR over the last 3 days
    # (~0.0059 / ~0.00066) stay far below AAPL2's (~0.143 / ~0.011), so JPM
    # ranks second. Its own 5/20 and 50/200 SMA checks are both `True` (the
    # gentle drift keeps recent averages above older ones), so JPM passes
    # every gate cleanly and is the one that should actually get bought. If
    # the golden-cross gate weren't wired into this loop, AAPL2 (the
    # top-ranked candidate on volatility/ATR) would be bought instead.
    closes = [500.0 - i * 1.5 for i in range(200)]
    base = closes[-1]
    for i in range(50):
        drift = base + i * 0.4
        noise = 0.5 if i % 2 == 0 else -0.9
        closes.append(drift + noise)

    start_date = date(2025, 6, 1)
    aapl2_bars = [
        HistoricalBar(start_date + timedelta(days=i), c + 1.0, c + 1.0, c - 1.0, c)
        for i, c in enumerate(closes)
    ]
    end_date = start_date + timedelta(days=len(closes) - 1)
    jpm_closes = [150.0 + i * 0.01 + (0.02 if i % 2 == 0 else -0.02) for i in range(len(closes))]
    jpm_bars = [
        HistoricalBar(start_date + timedelta(days=i), c + 0.05, c + 0.05, c - 0.05, c)
        for i, c in enumerate(jpm_closes)
    ]
    bars = {
        "SPY": [HistoricalBar(end_date, 400.0, 401.0, 399.0, 400.0)],
        "AAPL2": aapl2_bars,
        "JPM": jpm_bars,
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars.get(symbol, []) if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, stop_loss_pct=0.5, weekly_profit_goal=100_000.0,
        max_position_pct=0.5, min_position_pct=0.5, grace_period_days=5,
        rsi_overbought_threshold=70.0,
    )

    backtest_commands.cmd_backtest_run(
        "run_golden", tmp_path, starting_cash=10_000.0, start=end_date, end=end_date,
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={}, store=store, cfg=cfg,
        vol_window_days=2, atr_window_days=2,
    )

    paths = backtest_commands.resolve_run_paths("run_golden", tmp_path)
    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    assert [p.symbol for p in final_state.active_positions] == ["JPM"]


def test_cmd_backtest_run_fills_bonus_slot_from_prior_week_surplus(tmp_path):
    # max_active_positions=1 alone would allow only ONE of these two candidates
    # to be bought. prior_week_realized_pnl=1,200 with the default $500 goal
    # grants exactly 1 bonus slot (surplus $700 -> 1), so the effective cap is
    # 2 -- if the entries loop's free_slots calculation weren't updated to use
    # the same effective cap as evaluate_buy, only one symbol would get bought
    # here instead of both.
    bars = {
        "SPY": [HistoricalBar(date(2026, 1, 5), 400.0, 401.0, 399.0, 400.0)],
        "AAPL2": [
            HistoricalBar(date(2025, 12, 30), 98.0, 99.5, 97.5, 99.0),
            HistoricalBar(date(2025, 12, 31), 99.0, 100.5, 98.5, 100.0),
            HistoricalBar(date(2026, 1, 5), 100.0, 101.0, 99.0, 100.5),
        ],
        "JPM": [
            HistoricalBar(date(2025, 12, 30), 148.0, 149.5, 147.5, 149.0),
            HistoricalBar(date(2025, 12, 31), 149.0, 150.5, 148.5, 150.0),
            HistoricalBar(date(2026, 1, 5), 150.0, 151.0, 149.0, 150.5),
        ],
    }

    class FakeFetcher:
        def fetch_history(self, symbol, start, end):
            return [b for b in bars.get(symbol, []) if start <= b.date <= end]

    store = HistoricalPriceStore(FakeFetcher(), tmp_path / "cache")
    cfg = RiskConfig(
        max_active_positions=1, weekly_profit_goal=500.0, max_bonus_active_slots=2,
        max_position_pct=0.5, min_position_pct=0.5, stop_loss_pct=0.5, grace_period_days=5,
    )

    # `week` is seeded to match the single trading day's own ISO week
    # (2026-01-05 is ISO week "2026-W02") so `roll_week_if_needed` sees no
    # week transition on this run and leaves the seeded
    # `prior_week_realized_pnl` untouched -- a transition would otherwise
    # overwrite it with the seeded `week_realized_pnl` (0.0).
    paths = backtest_commands.resolve_run_paths("run_bonus", tmp_path)
    ledger.save_state(paths.ledger, PortfolioState(
        cash=10_000.0,
        week="2026-W02",
        prior_week_realized_pnl=1_200.0,
        month="2026-01",
        month_start_equity=10_000.0,
    ))

    backtest_commands.cmd_backtest_run(
        "run_bonus", tmp_path, starting_cash=10_000.0, start=date(2026, 1, 5), end=date(2026, 1, 5),
        candidate_symbols=["AAPL2", "JPM"], candidate_sectors={}, store=store, cfg=cfg,
        vol_window_days=2, atr_window_days=2,
    )

    final_state = ledger.load_state(paths.ledger, starting_cash=10_000.0)
    symbols = {p.symbol for p in final_state.active_positions}
    assert symbols == {"AAPL2", "JPM"}


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
