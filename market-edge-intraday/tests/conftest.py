"""Shared pytest fixtures.

Sets a SQLite test database BEFORE any app module is imported, builds small,
deterministic bar series, and provides a fresh schema per test.
"""
from __future__ import annotations

import os

# Must be set before importing app.database / app.config-derived engine.
os.environ.setdefault("INTRADAY_TEST_DB_URL", "sqlite:///./pytest_intraday.db")
os.environ.setdefault("MARKET_DATA_PROVIDER", "fixture")

from datetime import UTC, datetime  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402


def make_bars(
    opens, highs=None, lows=None, closes=None, volumes=None,
    start="2024-03-01 14:30", symbol="TEST", freq="15min",
) -> pd.DataFrame:
    """Build a UTC-indexed OHLCV frame from arrays (regular-session start)."""
    n = len(opens)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    opens = np.asarray(opens, dtype=float)
    closes = np.asarray(closes if closes is not None else opens, dtype=float)
    highs = np.asarray(highs if highs is not None else np.maximum(opens, closes) + 0.2, dtype=float)
    lows = np.asarray(lows if lows is not None else np.minimum(opens, closes) - 0.2, dtype=float)
    volumes = np.asarray(volumes if volumes is not None else np.full(n, 2_000_000.0), dtype=float)
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )
    df.attrs["symbol"] = symbol
    return df


def trending_session(symbol="TEST", base=100.0, step=0.15, n=26, vol=2_000_000.0) -> pd.DataFrame:
    """A clean intraday uptrend that should trigger breakout/momentum setups."""
    closes = base + np.arange(n) * step
    opens = closes - step * 0.3
    highs = closes + step * 0.4
    lows = opens - step * 0.4
    # Rising volume so relative-volume stays above the strategies' thresholds.
    volumes = np.linspace(vol, vol * 2.0, n)
    if n > 2:
        volumes[2] = vol * 2.5  # volume expansion on the breakout bar
    return make_bars(opens, highs, lows, closes, volumes, symbol=symbol)


def flat_benchmark(n=26, base=400.0) -> pd.DataFrame:
    closes = np.full(n, base) + np.random.default_rng(1).normal(0, 0.05, n)
    return make_bars(closes, symbol="SPY")


@pytest.fixture(autouse=True)
def fresh_db():
    """Reset the schema before each test so tables start empty."""
    from app.database import reset_db

    reset_db()
    yield


@pytest.fixture
def now_utc():
    return datetime(2024, 3, 1, 16, 0, tzinfo=UTC)
