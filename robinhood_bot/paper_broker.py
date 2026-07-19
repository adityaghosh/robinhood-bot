from dataclasses import dataclass
from datetime import datetime

from .portfolio import Portfolio


@dataclass
class Fill:
    timestamp: datetime
    side: str
    qty: int
    price: float


class PaperBroker:
    """Simulated broker: fills orders instantly at the given price, no fees/slippage."""

    def __init__(self, starting_cash: float):
        self.portfolio = Portfolio(cash=starting_cash)
        self.fills: list[Fill] = []

    def buy(self, timestamp: datetime, qty: int, price: float) -> None:
        before = self.portfolio.shares
        self.portfolio.buy(qty, price)
        filled = self.portfolio.shares - before
        if filled > 0:
            self.fills.append(Fill(timestamp, "BUY", filled, price))

    def sell(self, timestamp: datetime, qty: int, price: float) -> None:
        before = self.portfolio.shares
        self.portfolio.sell(qty, price)
        filled = before - self.portfolio.shares
        if filled > 0:
            self.fills.append(Fill(timestamp, "SELL", filled, price))
