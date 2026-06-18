import time
import pandas as pd
import yfinance as yf
from app.config import DATA_PERIOD, DATA_INTERVAL


def fetch_ohlcv(ticker: str, period: str = DATA_PERIOD, interval: str = DATA_INTERVAL) -> pd.DataFrame:
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
        df = df.dropna(subset=["close", "open", "high", "low", "volume"])
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        return df
    except Exception as e:
        print(f"  [warn] fetch_ohlcv {ticker}: {e}")
        return pd.DataFrame()


def fetch_multiple(tickers: list[str], delay: float = 0.2) -> dict[str, pd.DataFrame]:
    result = {}
    for ticker in tickers:
        df = fetch_ohlcv(ticker)
        if not df.empty:
            result[ticker] = df
        time.sleep(delay)
    return result


def fetch_ohlcv_range(ticker: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    try:
        df = yf.download(ticker, start=start, end=end, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
        df = df.dropna(subset=["close", "open", "high", "low", "volume"])
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        return df
    except Exception as e:
        print(f"  [warn] fetch_ohlcv_range {ticker}: {e}")
        return pd.DataFrame()
