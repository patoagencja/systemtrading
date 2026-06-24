"""Performance attribution helpers.

Every function accepts either a ``list[ClosedTrade]`` (the in-memory dataclass
from :mod:`app.domain`) or a pandas ``DataFrame`` of trades and groups net P&L
along a single dimension (strategy, sector, hour-of-day, ...). Each returns a
``DataFrame`` indexed by the grouping key with columns:

``net_pnl_pln``, ``num_trades``, ``win_rate``, ``profit_factor``.

All grouping is defensive: empty inputs yield an empty (but correctly shaped)
frame rather than raising.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import tzinfo
from typing import Any

import pandas as pd

try:  # ET timezone for hour bucketing.
    from zoneinfo import ZoneInfo

    _ET: tzinfo | None = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.11
    _ET = None

from app.logging_config import get_logger

logger = get_logger(__name__)

_RESULT_COLUMNS = ["net_pnl_pln", "num_trades", "win_rate", "profit_factor"]

_DAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _to_df(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Normalise trade input to a DataFrame.

    Accepts a pandas DataFrame (returned as-is, copied) or any iterable of
    ``ClosedTrade`` dataclasses / mappings. Enum-valued fields are coerced to
    their string value so grouping keys are clean.
    """
    if isinstance(trades, pd.DataFrame):
        return trades.copy()

    rows: list[dict[str, Any]] = []
    for t in trades or []:
        if is_dataclass(t) and not isinstance(t, type):
            row = asdict(t)
        elif isinstance(t, Mapping):
            row = dict(t)
        else:  # arbitrary object with attributes
            row = {k: getattr(t, k) for k in dir(t) if not k.startswith("_")}
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Coerce enum-like values to their string form for clean grouping.
    for col in df.columns:
        if df[col].map(lambda v: hasattr(v, "value")).any():
            df[col] = df[col].map(lambda v: v.value if hasattr(v, "value") else v)
    return df


def _pnl_column(df: pd.DataFrame) -> str:
    """Find the net PLN P&L column, tolerating naming variants."""
    for candidate in ("net_pnl_pln", "net_pnl", "net_pnl_usd"):
        if candidate in df.columns:
            return candidate
    return "net_pnl_pln"


def _profit_factor(pnl: pd.Series) -> float:
    """Gross win / gross loss; inf when only wins, 0 when empty/only losses."""
    gross_win = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    if gross_loss > 0:
        return gross_win / gross_loss
    return float("inf") if gross_win > 0 else 0.0


def _aggregate(df: pd.DataFrame, group_key: Any) -> pd.DataFrame:
    """Group ``df`` by ``group_key`` and compute the standard metric block."""
    if df.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)

    pnl_col = _pnl_column(df)
    pnl = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)
    work = df.copy()
    work["_pnl"] = pnl

    records: list[dict[str, Any]] = []
    index: list[Any] = []
    for key, grp in work.groupby(group_key, dropna=False):
        p = grp["_pnl"]
        num = int(len(p))
        records.append(
            {
                "net_pnl_pln": float(p.sum()),
                "num_trades": num,
                "win_rate": float((p > 0).sum()) / num if num else 0.0,
                "profit_factor": _profit_factor(p),
            }
        )
        index.append(key)

    out = pd.DataFrame.from_records(records, index=pd.Index(index))
    return out[_RESULT_COLUMNS]


def _entry_times_et(df: pd.DataFrame) -> pd.Series:
    """Return entry_time as an ET-localised datetime Series."""
    if "entry_time" not in df.columns:
        return pd.Series([pd.NaT] * len(df), index=df.index)
    ts = pd.to_datetime(df["entry_time"], errors="coerce", utc=True)
    if _ET is not None:
        with contextlib.suppress(TypeError, AttributeError):
            ts = ts.dt.tz_convert(_ET)
    return ts


def pnl_by_strategy(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Attribute net P&L per strategy."""
    df = _to_df(trades)
    if df.empty or "strategy" not in df.columns:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    return _aggregate(df, "strategy")


def pnl_by_sector(
    trades: Iterable[Any] | pd.DataFrame,
    sector_lookup: Mapping[str, str] | None,
) -> pd.DataFrame:
    """Attribute net P&L per sector using a ``symbol -> sector`` lookup."""
    df = _to_df(trades)
    if df.empty or "symbol" not in df.columns:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    lookup = sector_lookup or {}
    df = df.copy()
    df["sector"] = df["symbol"].map(lambda s: lookup.get(s, "UNKNOWN"))
    return _aggregate(df, "sector")


def pnl_by_hour(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Attribute net P&L by entry hour (America/New_York)."""
    df = _to_df(trades)
    if df.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    df = df.copy()
    df["entry_hour_et"] = _entry_times_et(df).dt.hour
    return _aggregate(df, "entry_hour_et")


def pnl_by_day_of_week(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Attribute net P&L by entry day-of-week (Monday..Sunday)."""
    df = _to_df(trades)
    if df.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    df = df.copy()
    dow = _entry_times_et(df).dt.dayofweek
    df["day_of_week"] = dow.map(lambda d: _DAY_NAMES[int(d)] if pd.notna(d) else "UNKNOWN")
    return _aggregate(df, "day_of_week")


def pnl_by_month(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Attribute net P&L by calendar month (``YYYY-MM``)."""
    df = _to_df(trades)
    if df.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    df = df.copy()
    df["month"] = _entry_times_et(df).dt.strftime("%Y-%m")
    return _aggregate(df, "month")


def pnl_by_year(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Attribute net P&L by calendar year."""
    df = _to_df(trades)
    if df.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    df = df.copy()
    df["year"] = _entry_times_et(df).dt.year
    return _aggregate(df, "year")


__all__ = [
    "pnl_by_strategy",
    "pnl_by_sector",
    "pnl_by_hour",
    "pnl_by_day_of_week",
    "pnl_by_month",
    "pnl_by_year",
]
