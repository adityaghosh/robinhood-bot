from typing import Protocol

import pandas as pd


class Strategy(Protocol):
    def signals(self, prices: pd.DataFrame) -> pd.Series:
        """Return a Series aligned to prices.index with values in {-1, 0, 1}
        meaning sell / hold / buy."""
        ...
