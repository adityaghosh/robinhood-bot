import pandas as pd

from robinhood_bot.paper_broker import PaperBroker
from robinhood_bot.strategies.moving_average import MovingAverageCrossover


def make_prices(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"Close": values}, index=idx)


def test_crossover_emits_buy_then_sell_signal():
    # fast(2)/slow(4) SMA: prices ramp up then down to force a clean cross both ways
    prices = make_prices([10, 10, 10, 10, 12, 14, 16, 18, 16, 14, 12, 10, 8, 6])
    strategy = MovingAverageCrossover(fast_window=2, slow_window=4)

    signals = strategy.signals(prices)

    assert (signals == 1).any(), "expected at least one buy signal"
    assert (signals == -1).any(), "expected at least one sell signal"
    first_buy = signals[signals == 1].index[0]
    first_sell = signals[signals == -1].index[0]
    assert first_buy < first_sell


def test_paper_broker_tracks_cash_and_fills():
    broker = PaperBroker(starting_cash=1000.0)
    ts = pd.Timestamp("2024-01-01")

    broker.buy(ts, qty=10, price=50.0)
    assert broker.portfolio.cash == 500.0
    assert broker.portfolio.shares == 10

    broker.sell(ts, qty=5, price=60.0)
    assert broker.portfolio.cash == 800.0
    assert broker.portfolio.shares == 5
    assert len(broker.fills) == 2


def test_paper_broker_buy_caps_at_available_cash():
    broker = PaperBroker(starting_cash=100.0)
    ts = pd.Timestamp("2024-01-01")

    broker.buy(ts, qty=10, price=50.0)  # only affordable qty is 2

    assert broker.portfolio.shares == 2
    assert broker.portfolio.cash == 0.0
