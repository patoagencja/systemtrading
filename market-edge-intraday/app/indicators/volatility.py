"""Volatility / range indicators (ATR, rolling realised vol)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(bars: pd.DataFrame) -> pd.Series:
    """Wilder's true range using prior close."""
    prev_close = bars["close"].shift(1)
    hl = bars["high"] - bars["low"]
    hc = (bars["high"] - prev_close).abs()
    lc = (bars["low"] - prev_close).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average true range (simple rolling mean of TR). Uses completed bars only."""
    tr = true_range(bars)
    return tr.rolling(window=period, min_periods=period).mean()


def realised_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling std of log returns over the last ``window`` completed bars."""
    rets = np.log(close / close.shift(1))
    return rets.rolling(window=window, min_periods=window).std()


def intraday_range_pct(bars: pd.DataFrame) -> pd.Series:
    """Per-bar high-low range as a fraction of close."""
    return (bars["high"] - bars["low"]) / bars["close"].replace(0, np.nan)
