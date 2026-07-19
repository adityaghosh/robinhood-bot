from dataclasses import dataclass, field


@dataclass
class Portfolio:
    cash: float
    shares: int = 0

    def equity(self, price: float) -> float:
        return self.cash + self.shares * price

    def buy(self, qty: int, price: float) -> None:
        cost = qty * price
        if cost > self.cash:
            qty = int(self.cash // price)
            cost = qty * price
        if qty <= 0:
            return
        self.cash -= cost
        self.shares += qty

    def sell(self, qty: int, price: float) -> None:
        qty = min(qty, self.shares)
        if qty <= 0:
            return
        self.cash += qty * price
        self.shares -= qty
