"""Compute a full intraday indicator snapshot for one symbol.

CRITICAL: every value is derived from *completed* bars only. The caller passes
the session's completed bars (oldest first). The "current" bar is the last row,
which is itself already closed. No value ever peeks at a forming or future bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.indicators.relative_strength import (
    benchmark_trend,
    relative_strength,
    return_from_open,
)
from app.indicators.volatility import atr, intraday_range_pct, realised_volatility
from app.indicators.vwap import session_vwap, vwap_deviation_atr, vwap_deviation_pct

# Number of 15-minute bars that make up the first 30 minutes (opening range).
OPENING_RANGE_BARS = 2
# Bars per hour at 15-minute cadence.
BARS_PER_HOUR = 4


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When there are no losses, RSI is 100.
    out = out.where(avg_loss != 0, 100.0)
    return out


def _safe_last(series: pd.Series, default: float = float("nan")) -> float:
    if series is None or series.empty:
        return default
    val = series.iloc[-1]
    return float(val) if pd.notna(val) else default


@dataclass
class IndicatorSnapshot:
    """All indicator values evaluated at the last completed bar."""

    symbol: str
    n_bars: int
    close: float
    open: float
    high: float
    low: float
    volume: float

    vwap: float = float("nan")
    vwap_dev_pct: float = float("nan")
    vwap_dev_atr: float = float("nan")

    ema9: float = float("nan")
    ema20: float = float("nan")
    ema50: float = float("nan")
    rsi14: float = float("nan")
    atr14: float = float("nan")

    relative_volume: float = float("nan")
    volume_sma20: float = float("nan")
    volume_percentile: float = float("nan")

    opening_range_high: float = float("nan")
    opening_range_low: float = float("nan")
    opening_range_width: float = float("nan")
    opening_range_complete: bool = False

    session_high: float = float("nan")
    session_low: float = float("nan")
    return_from_open: float = float("nan")
    return_last_hour: float = float("nan")
    volatility_20: float = float("nan")
    range_percentile: float = float("nan")

    rs_vs_spy: float = float("nan")
    rs_vs_sector: float = float("nan")
    spy_trend: float = 0.0

    distance_from_daily_close: float = float("nan")
    overnight_gap: float = float("nan")

    bar_close_in_top_quartile: bool = False
    bar_return: float = float("nan")
    extra: dict = field(default_factory=dict)


def compute_indicators(
    bars: pd.DataFrame,
    *,
    benchmark_bars: pd.DataFrame | None = None,
    sector_bars: pd.DataFrame | None = None,
    prev_session_close: float | None = None,
) -> IndicatorSnapshot:
    """Build an :class:`IndicatorSnapshot` from session bars (oldest first).

    ``bars`` columns: open, high, low, close, volume. Index is bar open time.
    """
    if bars is None or bars.empty:
        raise ValueError("compute_indicators requires at least one completed bar")

    symbol = str(bars.attrs.get("symbol", bars.get("symbol", pd.Series(["?"])).iloc[0]
                                if "symbol" in bars else "?"))
    n = len(bars)
    last = bars.iloc[-1]

    vwap_series = session_vwap(bars)
    vwap = _safe_last(vwap_series)
    atr_series = atr(bars, 14)
    atr14 = _safe_last(atr_series)
    # Fallback ATR for very short sessions: mean of available true ranges.
    if np.isnan(atr14) and n >= 2:
        from app.indicators.volatility import true_range

        atr14 = float(true_range(bars).mean())

    close = float(last["close"])
    snap = IndicatorSnapshot(
        symbol=symbol,
        n_bars=n,
        close=close,
        open=float(last["open"]),
        high=float(last["high"]),
        low=float(last["low"]),
        volume=float(last["volume"]),
        vwap=vwap,
        vwap_dev_pct=vwap_deviation_pct(close, vwap) if not np.isnan(vwap) else float("nan"),
        vwap_dev_atr=vwap_deviation_atr(close, vwap, atr14)
        if not np.isnan(vwap) and not np.isnan(atr14)
        else float("nan"),
        ema9=_safe_last(ema(bars["close"], 9)),
        ema20=_safe_last(ema(bars["close"], 20)),
        ema50=_safe_last(ema(bars["close"], 50)),
        rsi14=_safe_last(rsi(bars["close"], 14)),
        atr14=atr14,
    )

    # Volume metrics.
    vol = bars["volume"]
    vol_sma = vol.rolling(20, min_periods=1).mean()
    snap.volume_sma20 = _safe_last(vol_sma)
    if snap.volume_sma20 and snap.volume_sma20 > 0:
        snap.relative_volume = snap.volume / snap.volume_sma20
    if n >= 5:
        snap.volume_percentile = float((vol <= snap.volume).mean())

    # Opening range = first 30 minutes (first 2 bars).
    if n >= OPENING_RANGE_BARS:
        orb = bars.iloc[:OPENING_RANGE_BARS]
        snap.opening_range_high = float(orb["high"].max())
        snap.opening_range_low = float(orb["low"].min())
        snap.opening_range_width = snap.opening_range_high - snap.opening_range_low
        snap.opening_range_complete = True

    # Session stats.
    snap.session_high = float(bars["high"].max())
    snap.session_low = float(bars["low"].min())
    snap.return_from_open = return_from_open(bars)
    if n >= BARS_PER_HOUR + 1:
        ref = float(bars["close"].iloc[-(BARS_PER_HOUR + 1)])
        snap.return_last_hour = (close - ref) / ref if ref > 0 else float("nan")
    snap.volatility_20 = _safe_last(realised_volatility(bars["close"], 20))

    rng = intraday_range_pct(bars)
    if n >= 5:
        cur_rng = float(rng.iloc[-1]) if pd.notna(rng.iloc[-1]) else 0.0
        snap.range_percentile = float((rng.fillna(0) <= cur_rng).mean())

    # Candle quality: where does the close sit in the bar range.
    bar_range = snap.high - snap.low
    if bar_range > 0:
        pos = (close - snap.low) / bar_range
        snap.bar_close_in_top_quartile = pos >= 0.75
    snap.bar_return = (close - snap.open) / snap.open if snap.open > 0 else 0.0

    # Relative strength.
    if benchmark_bars is not None and not benchmark_bars.empty:
        snap.rs_vs_spy = relative_strength(bars, benchmark_bars)
        snap.spy_trend = benchmark_trend(benchmark_bars)
    if sector_bars is not None and not sector_bars.empty:
        snap.rs_vs_sector = relative_strength(bars, sector_bars)

    # Daily anchors.
    if prev_session_close and prev_session_close > 0:
        snap.distance_from_daily_close = (close - prev_session_close) / prev_session_close
        snap.overnight_gap = (float(bars["open"].iloc[0]) - prev_session_close) / prev_session_close

    return snap
