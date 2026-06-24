"""Session VWAP and deviation helpers.

VWAP is cumulative *within a single trading session*. All inputs are completed
bars only — callers must never pass a forming bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """Cumulative volume-weighted average price for the session.

    ``bars`` needs columns: high, low, close, volume — ordered by time and
    belonging to a single session. Returns a Series aligned to ``bars.index``.
    """
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    vol = bars["volume"].clip(lower=0)
    cum_vol = vol.cumsum()
    cum_pv = (typical * vol).cumsum()
    # Avoid divide-by-zero on a leading zero-volume bar.
    vwap = np.where(cum_vol > 0, cum_pv / cum_vol.replace(0, np.nan), typical)
    return pd.Series(vwap, index=bars.index, name="vwap")


def vwap_deviation_pct(close: float, vwap: float) -> float:
    """Signed percentage distance of price from VWAP."""
    if vwap <= 0:
        return 0.0
    return (close - vwap) / vwap * 100.0


def vwap_deviation_atr(close: float, vwap: float, atr: float) -> float:
    """Distance from VWAP measured in ATR units (signed)."""
    if atr <= 0:
        return 0.0
    return (close - vwap) / atr
