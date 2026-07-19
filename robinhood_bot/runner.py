from .config import Config
from .data_feed import fetch_price_history
from .paper_broker import PaperBroker
from .strategies.moving_average import MovingAverageCrossover


def run(config: Config) -> PaperBroker:
    prices = fetch_price_history(config.symbol, config.period, config.interval)
    strategy = MovingAverageCrossover(config.fast_window, config.slow_window)
    signals = strategy.signals(prices)

    broker = PaperBroker(config.starting_cash)

    for timestamp, signal in signals.items():
        price = prices.loc[timestamp, "Close"]
        if signal == 1:
            broker.buy(timestamp, config.trade_qty, price)
        elif signal == -1:
            broker.sell(timestamp, config.trade_qty, price)

    return broker
