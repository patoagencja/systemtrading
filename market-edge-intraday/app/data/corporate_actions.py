"""Corporate-action adjustments (splits) for OHLCV bars.

NOTE: The live providers (Alpaca, Massive/Polygon) are configured to return
split- and dividend-adjusted bars where the vendor supports it, so this module
is for any *extra* manual adjustment a research workflow may need. We never
fabricate split data — callers must supply real split events.

A split event is a dict::

    {"date": date | datetime | "YYYY-MM-DD", "ratio": float}

where ``ratio`` is the number of new shares per old share (e.g. a 4-for-1
split has ``ratio = 4.0``; a 1-for-10 reverse split has ``ratio = 0.1``).
Bars with an open time strictly *before* the split's effective date are
back-adjusted: prices divided by the cumulative ratio, volume multiplied.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from app.data.provider_base import normalize_bars
from app.logging_config import get_logger

logger = get_logger(__name__)


def _coerce_date(value: date | datetime | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
    return ts


def adjust_for_splits(df: pd.DataFrame, splits: list[dict]) -> pd.DataFrame:
    """Back-adjust OHLC and forward-adjust volume for ``splits``.

    Prices on bars before a split's effective date are divided by the
    cumulative split ratio; volume is multiplied by the same factor so that
    dollar volume is preserved. Returns a new normalized DataFrame; the input
    is not mutated. With no splits the input is returned unchanged (normalized).
    """
    df = normalize_bars(df)
    if df.empty or not splits:
        return df

    valid = []
    for ev in splits:
        try:
            eff = _coerce_date(ev["date"])
            ratio = float(ev["ratio"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed split event: %r", ev)
            continue
        if ratio <= 0:
            logger.warning("Skipping split with non-positive ratio: %r", ev)
            continue
        valid.append((eff, ratio))

    if not valid:
        return df

    valid.sort(key=lambda x: x[0])
    out = df.copy()
    # Cumulative factor applied to each historical bar = product of the ratios
    # of all splits that occur strictly after that bar's open time.
    factor = pd.Series(1.0, index=out.index)
    for eff, ratio in valid:
        factor.loc[out.index < eff] *= ratio

    for col in ("open", "high", "low", "close"):
        out[col] = out[col] / factor
    out["volume"] = out["volume"] * factor
    return out
