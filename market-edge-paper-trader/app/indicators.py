import pandas as pd
import numpy as np


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 30:
        return df

    df = df.copy()

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["sma20"] = close.rolling(20).mean()
    df["sma50"] = close.rolling(50).mean()
    df["sma100"] = close.rolling(100).mean()
    df["sma200"] = close.rolling(200).mean()
    df["ema20"] = close.ewm(span=20, adjust=False).mean()

    df["rsi14"] = _rsi(close, 14)
    df["atr14"] = _atr(high, low, close, 14)

    df["volume_sma20"] = volume.rolling(20).mean()
    df["volume_ratio"] = volume / df["volume_sma20"].replace(0, np.nan)

    df["high_20d"] = high.rolling(20).max()
    df["high_50d"] = high.rolling(50).max()
    df["low_20d"] = low.rolling(20).min()

    df["daily_return"] = close.pct_change()
    df["volatility_20d"] = df["daily_return"].rolling(20).std() * np.sqrt(252)

    df["distance_from_sma50_pct"] = (close - df["sma50"]) / df["sma50"] * 100
    df["distance_from_sma200_pct"] = (close - df["sma200"]) / df["sma200"] * 100

    df["return_5d"] = close.pct_change(5)

    return df


def get_latest_row(df: pd.DataFrame) -> pd.Series | None:
    if df.empty:
        return None
    last = df.iloc[-1]
    required = ["close", "sma200", "sma50", "rsi14", "atr14", "volume_ratio"]
    if any(pd.isna(last.get(col)) for col in required):
        return None
    return last
