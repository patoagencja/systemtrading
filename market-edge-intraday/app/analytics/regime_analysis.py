"""Market-regime classification and regime-conditional performance.

The benchmark (SPY) daily series is classified into UPTREND / DOWNTREND / CHOP
using price relative to its 20- and 50-day SMAs plus realised volatility, then
trades are attributed to the regime in force on their entry date.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.attribution import _aggregate, _entry_times_et, _to_df
from app.logging_config import get_logger

logger = get_logger(__name__)

UPTREND = "UPTREND"
DOWNTREND = "DOWNTREND"
CHOP = "CHOP"

_SMA_FAST = 20
_SMA_SLOW = 50
_VOL_WINDOW = 20
# Annualised realised-vol threshold above which a trend is treated as choppy.
_HIGH_VOL_ANNUAL = 0.25


def _close_column(df: pd.DataFrame) -> str:
    for candidate in ("close", "adj_close", "Close", "Adj Close"):
        if candidate in df.columns:
            return candidate
    # Fall back to the first numeric column.
    numeric = df.select_dtypes("number").columns
    return numeric[0] if len(numeric) else df.columns[0]


def classify_spy_regime(spy_daily: pd.DataFrame) -> pd.Series:
    """Classify each SPY daily bar into UPTREND / DOWNTREND / CHOP.

    Parameters
    ----------
    spy_daily:
        Daily OHLC(V) frame with a DatetimeIndex (or a ``date``/``timestamp``
        column) and a close column.

    Logic
    -----
    * UPTREND  : close > SMA20 > SMA50 and realised vol is not elevated.
    * DOWNTREND: close < SMA20 < SMA50 and realised vol is not elevated.
    * CHOP     : everything else, or whenever realised vol is elevated, or
      before enough history exists to compute the slow SMA.

    Returns a string ``Series`` aligned to the input index (normalised to
    midnight). Empty input yields an empty Series.
    """
    if spy_daily is None or len(spy_daily) == 0:
        return pd.Series(dtype="object")

    df = spy_daily.copy()
    # Establish a DatetimeIndex.
    if not isinstance(df.index, pd.DatetimeIndex):
        for col in ("date", "timestamp", "Date"):
            if col in df.columns:
                df = df.set_index(pd.to_datetime(df[col], utc=False, errors="coerce"))
                break
        else:
            df.index = pd.to_datetime(df.index, errors="coerce")
    df = df.sort_index()

    close = pd.to_numeric(df[_close_column(df)], errors="coerce")
    sma_fast = close.rolling(_SMA_FAST, min_periods=_SMA_FAST).mean()
    sma_slow = close.rolling(_SMA_SLOW, min_periods=_SMA_SLOW).mean()

    rets = close.pct_change()
    realised_vol = rets.rolling(_VOL_WINDOW, min_periods=_VOL_WINDOW).std() * np.sqrt(252.0)
    high_vol = realised_vol > _HIGH_VOL_ANNUAL

    up = (close > sma_fast) & (sma_fast > sma_slow)
    down = (close < sma_fast) & (sma_fast < sma_slow)

    regime = pd.Series(CHOP, index=df.index, dtype="object")
    regime[up & ~high_vol.fillna(True)] = UPTREND
    regime[down & ~high_vol.fillna(True)] = DOWNTREND
    # Insufficient history -> CHOP (already the default), but mark NaN SMA rows.
    regime[sma_slow.isna()] = CHOP

    regime.index = regime.index.normalize()
    regime.name = "spy_regime"
    return regime


def performance_by_regime(
    trades: Iterable[Any] | pd.DataFrame,
    spy_regime: pd.Series,
) -> pd.DataFrame:
    """Attribute trade performance to the SPY regime on the entry date.

    Returns a DataFrame indexed by regime with the standard attribution block
    (``net_pnl_pln``, ``num_trades``, ``win_rate``, ``profit_factor``). Trades
    whose entry date has no regime mapping are bucketed under ``UNKNOWN``.
    """
    columns = ["net_pnl_pln", "num_trades", "win_rate", "profit_factor"]
    df = _to_df(trades)
    if df.empty:
        return pd.DataFrame(columns=columns)

    entry_dates = _entry_times_et(df).dt.normalize()
    # Make the regime lookup tz-naive-date keyed for robust alignment.
    if spy_regime is None or len(spy_regime) == 0:
        df = df.copy()
        df["regime"] = "UNKNOWN"
        return _aggregate(df, "regime")

    reg = spy_regime.copy()
    reg.index = pd.to_datetime(reg.index).tz_localize(None).normalize()
    lookup = {d.date(): v for d, v in reg.items()}

    def _map(ts: Any) -> str:
        if pd.isna(ts):
            return "UNKNOWN"
        try:
            key = ts.tz_localize(None).normalize().date() if ts.tzinfo else ts.normalize().date()
        except (AttributeError, TypeError):
            return "UNKNOWN"
        return lookup.get(key, "UNKNOWN")

    df = df.copy()
    df["regime"] = entry_dates.map(_map)
    return _aggregate(df, "regime")


__all__ = [
    "classify_spy_regime",
    "performance_by_regime",
    "UPTREND",
    "DOWNTREND",
    "CHOP",
]
