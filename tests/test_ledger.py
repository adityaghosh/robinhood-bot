import json
from datetime import date

from robinhood_bot import ledger
from robinhood_bot.portfolio_state import Position, PositionStatus, PortfolioState


def test_load_state_returns_fresh_state_when_file_missing(tmp_path):
    path = tmp_path / "ledger.json"
    state = ledger.load_state(path, starting_cash=10_000.0)
    assert state.cash == 10_000.0
    assert state.active_positions == []


def test_save_and_load_round_trip_preserves_positions(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(
        cash=8_000.0,
        active_positions=[
            Position(
                "AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.WAITING,
                underwater_since=date(2026, 7, 5),
            )
        ],
        long_hold_positions=[
            Position("TSLA", 5, 200.0, date(2026, 6, 1), PositionStatus.LONG_HOLD)
        ],
        month="2026-07",
        month_start_equity=10_000.0,
    )
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.cash == 8_000.0
    assert loaded.month == "2026-07"
    assert loaded.active_positions[0].symbol == "AAPL"
    assert loaded.active_positions[0].status == PositionStatus.WAITING
    assert loaded.active_positions[0].underwater_since == date(2026, 7, 5)
    assert loaded.long_hold_positions[0].symbol == "TSLA"


def test_append_trade_log_writes_header_once(tmp_path):
    path = tmp_path / "trade_log.csv"
    ledger.append_trade_log(path, {
        "timestamp": "2026-07-01", "action": "BUY", "symbol": "AAPL",
        "qty": 10, "price": 100.0, "reason": "test",
    })
    ledger.append_trade_log(path, {
        "timestamp": "2026-07-02", "action": "SELL", "symbol": "AAPL",
        "qty": 10, "price": 110.0, "reason": "test",
    })
    contents = path.read_text().splitlines()
    assert contents[0] == "timestamp,action,symbol,qty,price,reason"
    assert len(contents) == 3


def test_save_and_load_round_trip_preserves_week_tracking(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(cash=8_000.0, week="2026-W28", week_realized_pnl=350.0)
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.week == "2026-W28"
    assert loaded.week_realized_pnl == 350.0


def test_save_and_load_round_trip_preserves_sector(tmp_path):
    path = tmp_path / "ledger.json"
    original = PortfolioState(
        cash=8_000.0,
        active_positions=[
            Position("AAPL", 10, 100.0, date(2026, 7, 1), PositionStatus.ACTIVE, sector="Technology")
        ],
    )
    ledger.save_state(path, original)
    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].sector == "Technology"


def test_load_state_defaults_missing_sector_to_none_for_old_ledger_files(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "cash": 5_000.0,
        "active_positions": [{
            "symbol": "AAPL", "qty": 10, "entry_price": 100.0,
            "entry_date": "2026-07-01", "status": "ACTIVE", "underwater_since": None,
        }],
        "long_hold_positions": [],
        "month": "", "month_start_equity": 0.0, "week": "", "week_realized_pnl": 0.0,
    }))

    loaded = ledger.load_state(path, starting_cash=0.0)

    assert loaded.active_positions[0].sector is None
