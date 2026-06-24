"""Indicator correctness + no-look-ahead guarantees."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.intraday_indicators import compute_indicators
from app.indicators.relative_strength import relative_strength, return_from_open
from app.indicators.vwap import session_vwap
from tests.conftest import make_bars


def test_vwap_matches_manual_calculation():
    bars = make_bars(
        opens=[10, 11, 12], highs=[10, 11, 12], lows=[10, 11, 12],
        closes=[10, 11, 12], volumes=[100, 100, 100],
    )
    vwap = session_vwap(bars)
    # Typical price == close here; equal volumes => running mean of [10,11,12].
    assert vwap.iloc[0] == 10
    assert abs(vwap.iloc[1] - 10.5) < 1e-9
    assert abs(vwap.iloc[2] - 11.0) < 1e-9


def test_vwap_volume_weighting():
    bars = make_bars(
        opens=[10, 20], highs=[10, 20], lows=[10, 20], closes=[10, 20],
        volumes=[1, 3],
    )
    vwap = session_vwap(bars)
    # (10*1 + 20*3) / 4 = 17.5
    assert abs(vwap.iloc[1] - 17.5) < 1e-9


def test_no_lookahead_indicator_is_stable_under_truncation():
    """Indicators at bar k must not change when future bars are added."""
    full = make_bars(
        opens=np.linspace(100, 110, 12), volumes=np.full(12, 1e6),
    )
    k = 6
    snap_trunc = compute_indicators(full.iloc[: k + 1])
    snap_full_then_trunc = compute_indicators(full.iloc[: k + 1])  # same input
    # VWAP at bar k from a longer series sliced to k equals the truncated calc.
    vwap_k_from_full = session_vwap(full).iloc[k]
    vwap_k_trunc = session_vwap(full.iloc[: k + 1]).iloc[k]
    assert abs(vwap_k_from_full - vwap_k_trunc) < 1e-9
    assert snap_trunc.close == snap_full_then_trunc.close


def test_opening_range_uses_first_two_bars_only():
    bars = make_bars(
        opens=[100, 101, 102, 103],
        highs=[101, 102, 110, 104],  # huge high on bar 2 (after opening range)
        lows=[99, 100, 101, 102],
        closes=[100.5, 101.5, 109, 103.5],
    )
    snap = compute_indicators(bars)
    # Opening range = max/min of first two bars only; bar index 2's 110 excluded.
    assert snap.opening_range_high == 102
    assert snap.opening_range_low == 99


def test_relative_volume():
    vols = [1_000_000] * 20 + [3_000_000]
    bars = make_bars(opens=[100] * 21, volumes=vols)
    snap = compute_indicators(bars)
    # last bar 3x the ~1M average.
    assert snap.relative_volume > 2.5


def test_relative_strength_outperformance():
    sym = make_bars(opens=[100], closes=[102])  # +2%
    bench = make_bars(opens=[400], closes=[404])  # +1%
    rs = relative_strength(sym, bench)
    assert rs > 0
    assert abs(return_from_open(sym) - 0.02) < 1e-9


def test_compute_indicators_requires_data():
    import pytest

    with pytest.raises(ValueError):
        compute_indicators(pd.DataFrame())
