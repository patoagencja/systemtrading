"""Data quality validation for intraday bars."""
import datetime
import logging
from typing import Optional

import pandas as pd
import numpy as np

from app.intraday.session_manager import NY_TZ

log = logging.getLogger(__name__)


class DataQualityChecker:
    """Validate and clean intraday OHLCV DataFrames."""

    @staticmethod
    def validate_bar(row: pd.Series) -> tuple[bool, str]:
        """Check a single bar for validity. Returns (valid, reason_if_invalid)."""
        for col in ("open", "high", "low", "close", "volume"):
            if col not in row.index:
                return False, f"missing column: {col}"
            if pd.isna(row[col]):
                return False, f"NaN in {col}"

        o, h, l, c, v = row["open"], row["high"], row["low"], row["close"], row["volume"]

        if o <= 0:
            return False, f"open <= 0: {o}"
        if h < max(o, c):
            return False, f"high {h} < max(open,close) {max(o,c)}"
        if l > min(o, c):
            return False, f"low {l} > min(open,close) {min(o,c)}"
        if v < 0:
            return False, f"volume < 0: {v}"

        # Timestamp checks
        ts = row.name
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            return False, "timestamp is timezone-naive"
        now_utc = datetime.datetime.now(tz=datetime.timezone.utc)
        if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
            ts_utc = ts.astimezone(datetime.timezone.utc)
            if ts_utc > now_utc + datetime.timedelta(minutes=5):
                return False, f"timestamp in future: {ts}"

        return True, ""

    @staticmethod
    def validate_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        """Filter invalid bars. Returns (clean_df, warnings)."""
        if df.empty:
            return df, []
        warnings: list[str] = []
        valid_mask = []
        for ts, row in df.iterrows():
            ok, reason = DataQualityChecker.validate_bar(row)
            valid_mask.append(ok)
            if not ok:
                warnings.append(f"{ts}: {reason}")
        clean = df[valid_mask].copy()
        if len(warnings) > 0:
            log.debug(f"Removed {len(warnings)} invalid bars")
        return clean, warnings

    @staticmethod
    def check_for_duplicates(df: pd.DataFrame) -> pd.DataFrame:
        """Remove duplicate timestamps, keep last."""
        if df.empty:
            return df
        return df[~df.index.duplicated(keep="last")]

    @staticmethod
    def check_bar_completeness(df: pd.DataFrame, interval_minutes: int = 30) -> dict:
        """Return stats about bar completeness in regular session."""
        if df.empty:
            return {"sessions": 0, "total_bars": 0, "expected_bars": 0, "missing_pct": 0.0}
        bars_per_session = int(390 / interval_minutes)
        sessions = df.groupby(df.index.date)
        total_bars = len(df)
        session_count = len(sessions)
        expected = session_count * bars_per_session
        missing = max(0, expected - total_bars)
        return {
            "sessions": session_count,
            "total_bars": total_bars,
            "expected_bars": expected,
            "missing_bars": missing,
            "missing_pct": round(missing / expected * 100, 2) if expected > 0 else 0.0,
        }

    @staticmethod
    def is_regular_session_bar(ts: datetime.datetime, interval_minutes: int = 30) -> bool:
        """Return True if bar falls within regular session 09:30-16:00 ET."""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=NY_TZ)
        else:
            ts = ts.astimezone(NY_TZ)
        t = ts.time()
        return datetime.time(9, 30) <= t < datetime.time(16, 0)
