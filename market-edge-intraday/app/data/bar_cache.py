"""Parquet-backed historical bar cache + DuckDB analytics helper.

Bars are stored one Parquet file per symbol under ``settings.parquet_dir``
(``{parquet_dir}/{symbol}.parquet``). Stored frames honor the strict provider
contract (UTC index = bar open time, OHLCV columns). All functions are robust
when the cache directory is empty or missing.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import settings
from app.data.provider_base import empty_bars, normalize_bars
from app.logging_config import get_logger

logger = get_logger(__name__)

_TS_COL = "timestamp"


def _cache_dir() -> Path:
    path = Path(settings.parquet_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _symbol_path(symbol: str) -> Path:
    return _cache_dir() / f"{symbol.upper()}.parquet"


def has_bars(symbol: str) -> bool:
    """True if a Parquet file exists for ``symbol``."""
    return _symbol_path(symbol).exists()


def save_bars(symbol: str, df: pd.DataFrame) -> None:
    """Overwrite the cached bars for ``symbol`` with ``df`` (normalized)."""
    df = normalize_bars(df)
    path = _symbol_path(symbol)
    if df.empty:
        logger.debug("save_bars: nothing to write for %s", symbol)
        return
    out = df.copy()
    out.index.name = _TS_COL
    out.reset_index().to_parquet(path, index=False)
    logger.debug("save_bars: wrote %d bars for %s -> %s", len(out), symbol, path)


def load_bars(
    symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Load cached bars for ``symbol``, optionally sliced to ``[start, end]``.

    Returns an empty (correctly-shaped) DataFrame if nothing is cached.
    """
    path = _symbol_path(symbol)
    if not path.exists():
        return empty_bars()
    raw = pd.read_parquet(path)
    if _TS_COL in raw.columns:
        raw = raw.set_index(_TS_COL)
    df = normalize_bars(raw)
    if start is not None:
        df = df[df.index >= pd.Timestamp(start).tz_convert("UTC")
                if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index <= (pd.Timestamp(end).tz_convert("UTC")
                if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC"))]
    return df


def merge_bars(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """Merge ``df`` into the cache for ``symbol``, de-duping on timestamp.

    Newer rows (from ``df``) win on collision. Returns the merged frame.
    """
    df = normalize_bars(df)
    existing = load_bars(symbol)
    if existing.empty:
        merged = df
    elif df.empty:
        merged = existing
    else:
        merged = pd.concat([existing, df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    save_bars(symbol, merged)
    return merged


def query(sql: str) -> pd.DataFrame:
    """Run a read-only DuckDB query over the Parquet cache.

    The Parquet files are exposed via the ``read_parquet`` glob; reference them
    in SQL as e.g. ``read_parquet('{parquet_dir}/AAPL.parquet')``. Returns a
    pandas DataFrame. Raises if DuckDB is unavailable.
    """
    import duckdb

    db_path = settings.duckdb_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(db_path, read_only=False)
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()
