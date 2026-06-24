"""Bar-quality validation used as a *global* trade filter.

The engine refuses to act on bad data, so these checks are deliberately
conservative: any detected anomaly flips ``ValidationResult.ok`` to False and
records a human-readable reason. Validation operates on the strict provider
DataFrame contract (UTC index = bar open time, OHLCV columns).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.data.provider_base import BAR_COLUMNS

_INTERVAL_MIN_DEFAULT = 15


@dataclass
class ValidationResult:
    """Outcome of validating a bars DataFrame for one symbol."""

    ok: bool
    reasons: list[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        self.ok = False
        self.reasons.append(reason)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def is_bar_complete(bar_open_time: datetime, now: datetime, interval_minutes: int = 15) -> bool:
    """True if the bar opening at ``bar_open_time`` has fully closed by ``now``.

    A 15-minute bar opening at 09:30 closes at 09:45; it is complete once
    ``now >= 09:45``. Both datetimes must be tz-aware.
    """
    if bar_open_time.tzinfo is None:
        bar_open_time = bar_open_time.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    close_time = bar_open_time + pd.Timedelta(minutes=interval_minutes)
    return now >= close_time


def validate_bars(
    df: pd.DataFrame,
    symbol: str,
    max_staleness_seconds: int | None = None,
    now: datetime | None = None,
    interval_minutes: int = _INTERVAL_MIN_DEFAULT,
) -> ValidationResult:
    """Validate one symbol's bars. Returns a :class:`ValidationResult`.

    Checks: empty/missing data, wrong columns, NaNs, non-monotonic or
    duplicated timestamps, non-UTC index, negative/zero prices, ``high < low``,
    OHLC inconsistency, zero-volume bars, grid gaps and last-bar staleness.
    """
    result = ValidationResult(ok=True)
    now = now or _utcnow()

    if df is None or len(df) == 0:
        result.add("empty: no bars")
        return result

    missing = [c for c in BAR_COLUMNS if c not in df.columns]
    if missing:
        result.add(f"columns: missing {missing}")
        return result

    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        result.add("index: not tz-aware DatetimeIndex")
        return result

    if not idx.is_monotonic_increasing:
        result.add("timestamps: not monotonically increasing")
    if idx.has_duplicates:
        result.add("timestamps: duplicate bar timestamps")

    ohlcv = df[BAR_COLUMNS]
    if ohlcv.isna().any().any():
        result.add("values: NaNs present")

    prices = df[["open", "high", "low", "close"]]
    finite_prices = prices.replace([np.inf, -np.inf], np.nan).dropna()
    if (finite_prices <= 0).any().any():
        result.add("prices: non-positive open/high/low/close")
    if (df["high"] < df["low"]).any():
        result.add("prices: high < low")
    # OHLC consistency: high must be the max, low the min of the bar.
    hi_bad = (df["high"] < df[["open", "close"]].max(axis=1)).any()
    lo_bad = (df["low"] > df[["open", "close"]].min(axis=1)).any()
    if hi_bad or lo_bad:
        result.add("prices: OHLC inconsistent (high/low not enclosing open/close)")

    if (df["volume"] < 0).any():
        result.add("volume: negative volume")
    if (df["volume"] == 0).any():
        result.add("volume: zero-volume bar(s)")

    # Grid gaps: consecutive bars should be exactly one interval apart.
    if len(idx) >= 2 and idx.is_monotonic_increasing:
        deltas = idx.to_series().diff().dropna()
        step = pd.Timedelta(minutes=interval_minutes)
        # Gaps that are not exact multiples of the interval, or larger than the
        # interval (a missing bar). We tolerate the overnight/session boundary
        # by only flagging intra-cluster gaps larger than one interval.
        irregular = deltas[(deltas % step) != pd.Timedelta(0)]
        if not irregular.empty:
            result.add("grid: bar spacing not a multiple of the interval")
        # A single missing bar inside a run shows as a 2x interval gap; we flag
        # only modest gaps (<= 1 day) to avoid penalising the overnight break.
        intraday_gap = deltas[(deltas > step) & (deltas <= pd.Timedelta(hours=6))]
        if not intraday_gap.empty:
            result.add(f"grid: {len(intraday_gap)} missing intraday bar gap(s)")

    if max_staleness_seconds is not None:
        last_open = idx[-1].to_pydatetime()
        last_close = last_open + pd.Timedelta(minutes=interval_minutes)
        age = (now - last_close).total_seconds()
        if age > max_staleness_seconds:
            result.add(
                f"stale: last bar closed {age:.0f}s ago (> {max_staleness_seconds}s)"
            )

    return result
