import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest
from app.scoring import (
    score_mean_reversion,
    score_momentum_breakout,
    score_pullback_trend,
    score_etf_relative_strength,
)


def base_row(**kwargs) -> pd.Series:
    defaults = {
        "close": 100.0,
        "open": 98.0,
        "sma50": 95.0,
        "sma200": 85.0,
        "rsi14": 50.0,
        "atr14": 2.0,
        "volume_ratio": 1.2,
        "high_20d": 105.0,
        "low_20d": 90.0,
        "distance_from_sma50_pct": 5.0,
        "distance_from_sma200_pct": 17.6,
        "daily_return": 0.01,
        "volatility_20d": 0.30,
        "return_5d": -0.05,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


class TestScoringRange:
    """All scoring functions must return a value in [0, 100]."""

    def test_mean_reversion_range(self):
        for rsi in [20, 30, 35, 50, 70]:
            for ret5 in [-0.10, -0.05, -0.03, 0]:
                row = base_row(rsi14=rsi, return_5d=ret5)
                score = score_mean_reversion(row)
                assert 0 <= score <= 100, f"score={score} out of range for rsi={rsi}"

    def test_momentum_breakout_range(self):
        for rsi in [50, 60, 70, 80]:
            for vol in [1.0, 1.5, 2.5]:
                row = base_row(rsi14=rsi, volume_ratio=vol)
                score = score_momentum_breakout(row)
                assert 0 <= score <= 100, f"score={score} out of range"

    def test_pullback_trend_range(self):
        for dist in [-3.0, 0.0, 2.0, 5.0]:
            row = base_row(distance_from_sma50_pct=dist)
            score = score_pullback_trend(row)
            assert 0 <= score <= 100, f"score={score} for dist={dist}"

    def test_etf_rs_range(self):
        row = base_row()
        for s_ret, spy_ret in [(0.15, 0.05), (0.08, 0.03), (0.02, 0.01)]:
            score = score_etf_relative_strength(row, s_ret, spy_ret)
            assert 0 <= score <= 100, f"score={score}"

    def test_perfect_mean_reversion_row_high_score(self):
        row = base_row(
            rsi14=22,
            return_5d=-0.09,
            volume_ratio=1.8,
            volatility_20d=0.35,
            distance_from_sma200_pct=12.0,
        )
        score = score_mean_reversion(row)
        assert score >= 70, f"Expected high score, got {score}"

    def test_weak_signal_low_score(self):
        row = base_row(
            rsi14=49,
            return_5d=-0.01,
            volume_ratio=0.7,
            volatility_20d=0.70,
            distance_from_sma200_pct=1.0,
        )
        score = score_mean_reversion(row)
        assert score < 75, f"Expected low score, got {score}"
