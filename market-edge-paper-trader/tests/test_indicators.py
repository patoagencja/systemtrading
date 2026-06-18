import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pandas as pd
import numpy as np
from app.indicators import compute_indicators, get_latest_row


def make_df(n=250) -> pd.DataFrame:
    """Create a synthetic OHLCV dataframe of n bars."""
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    close = np.maximum(close, 1.0)
    high = close * (1 + np.abs(np.random.randn(n) * 0.005))
    low = close * (1 - np.abs(np.random.randn(n) * 0.005))
    open_ = close + np.random.randn(n) * 0.2
    volume = np.random.randint(1_000_000, 5_000_000, size=n).astype(float)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


class TestIndicators:
    def test_returns_dataframe(self):
        df = make_df()
        result = compute_indicators(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(df)

    def test_required_columns_present(self):
        df = make_df(250)
        result = compute_indicators(df)
        expected = [
            "sma20", "sma50", "sma100", "sma200", "ema20",
            "rsi14", "atr14", "volume_sma20", "volume_ratio",
            "high_20d", "high_50d", "low_20d",
            "daily_return", "volatility_20d",
            "distance_from_sma50_pct", "distance_from_sma200_pct",
        ]
        for col in expected:
            assert col in result.columns, f"Missing column: {col}"

    def test_indicators_not_empty_after_sufficient_data(self):
        df = make_df(250)
        result = compute_indicators(df)
        last = result.iloc[-1]
        for col in ["sma200", "sma50", "rsi14", "atr14"]:
            assert not pd.isna(last[col]), f"{col} is NaN on last row"

    def test_sma50_nan_with_insufficient_data(self):
        df = make_df(30)
        result = compute_indicators(df)
        # sma50 requires 50 bars — should be NaN for first 49 rows
        assert pd.isna(result["sma50"].iloc[0])

    def test_sma200_nan_with_insufficient_data(self):
        df = make_df(250)
        result = compute_indicators(df)
        # First 199 rows should have NaN for sma200
        assert pd.isna(result["sma200"].iloc[0])

    def test_get_latest_row_returns_none_for_short_df(self):
        df = make_df(50)
        result = compute_indicators(df)
        row = get_latest_row(result)
        # sma200 will be NaN → should return None
        assert row is None

    def test_get_latest_row_returns_series_for_long_df(self):
        df = make_df(250)
        result = compute_indicators(df)
        row = get_latest_row(result)
        assert row is not None
        assert isinstance(row, pd.Series)

    def test_volume_ratio_positive(self):
        df = make_df(250)
        result = compute_indicators(df)
        valid = result["volume_ratio"].dropna()
        assert (valid > 0).all()

    def test_rsi_bounded(self):
        df = make_df(250)
        result = compute_indicators(df)
        valid = result["rsi14"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()
