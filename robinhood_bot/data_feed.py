import pandas as pd
import yfinance as yf


def fetch_price_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Download OHLCV bars for a symbol. Index is datetime, columns include 'Close'."""
    df = yf.Ticker(symbol).history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"no price data returned for {symbol}")
    return df
