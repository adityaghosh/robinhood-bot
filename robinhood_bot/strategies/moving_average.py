import pandas as pd


class MovingAverageCrossover:
    """Buy when the fast SMA crosses above the slow SMA, sell on the reverse cross."""

    def __init__(self, fast_window: int, slow_window: int):
        self.fast_window = fast_window
        self.slow_window = slow_window

    def signals(self, prices: pd.DataFrame) -> pd.Series:
        close = prices["Close"]
        fast = close.rolling(self.fast_window).mean()
        slow = close.rolling(self.slow_window).mean()

        above = fast > slow
        prev_above = above.shift(1, fill_value=False)
        crossed_up = above & ~prev_above
        crossed_down = ~above & prev_above

        signal = pd.Series(0, index=close.index)
        signal[crossed_up] = 1
        signal[crossed_down] = -1
        return signal
