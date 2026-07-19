from dataclasses import dataclass


@dataclass
class Config:
    symbol: str = "AAPL"
    period: str = "6mo"
    interval: str = "1d"
    starting_cash: float = 10_000.0
    fast_window: int = 10
    slow_window: int = 30
    trade_qty: int = 10
