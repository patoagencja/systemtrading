"""Direct strategy + scoring tests on crafted indicator snapshots (fast, no I/O)."""
from __future__ import annotations

from datetime import time

from app.indicators.intraday_indicators import IndicatorSnapshot
from app.strategies.base import StrategyContext
from app.strategies.opening_range_breakout import OpeningRangeBreakout
from app.strategies.relative_strength_momentum import RelativeStrengthMomentum
from app.strategies.scoring import score_signal
from app.strategies.volume_expansion import VolumeExpansionMomentum
from app.strategies.vwap_mean_reversion import VwapMeanReversion


def _ctx(et=time(11, 0), i=6):
    return StrategyContext(now_utc=__import__("datetime").datetime(2024, 3, 1, 16, 0),
                           now_et=et, session_bar_index=i, bars_remaining=10, spy_trend=0.2)


def _snap(**kw) -> IndicatorSnapshot:
    base = dict(
        symbol="X", n_bars=10, close=101.0, open=100.0, high=101.2, low=99.9,
        volume=5_000_000.0, vwap=100.0, vwap_dev_pct=1.0, vwap_dev_atr=0.5,
        ema9=100.8, ema20=100.2, ema50=100.0, rsi14=55.0, atr14=0.5,
        relative_volume=3.0, volume_sma20=1_600_000.0, volume_percentile=0.9,
        opening_range_high=100.5, opening_range_low=99.5, opening_range_width=1.0,
        opening_range_complete=True, session_high=101.2, session_low=99.5,
        return_from_open=0.01, return_last_hour=0.005, volatility_20=0.01,
        range_percentile=0.6, rs_vs_spy=0.005, rs_vs_sector=0.004, spy_trend=0.2,
        distance_from_daily_close=0.01, overnight_gap=0.0,
        bar_close_in_top_quartile=True, bar_return=0.008,
    )
    base.update(kw)
    return IndicatorSnapshot(**base)


def test_volume_expansion_fires_on_clean_setup():
    strat = VolumeExpansionMomentum()
    cand = strat.generate(_snap(), _ctx())
    assert cand is not None
    assert cand.stop_price < cand.reference_price < cand.target_price
    assert cand.score > 0


def test_volume_expansion_rejects_low_rvol():
    assert VolumeExpansionMomentum().generate(_snap(relative_volume=1.0), _ctx()) is None


def test_volume_expansion_rejects_weak_close():
    assert VolumeExpansionMomentum().generate(
        _snap(bar_close_in_top_quartile=False), _ctx()) is None


def test_volume_expansion_rejects_below_vwap():
    assert VolumeExpansionMomentum().generate(_snap(close=99.0, vwap=100.0), _ctx()) is None


def test_volume_expansion_rejects_overextended():
    assert VolumeExpansionMomentum().generate(_snap(vwap_dev_atr=3.0), _ctx()) is None


def test_volume_expansion_rejects_when_too_late():
    assert VolumeExpansionMomentum().generate(_snap(), _ctx(et=time(15, 0))) is None


def test_volume_expansion_rejects_in_spy_crash():
    assert VolumeExpansionMomentum().generate(_snap(spy_trend=-0.8), _ctx()) is None


def test_orb_fires_and_rejects_overextended():
    strat = OpeningRangeBreakout()
    # close just above ORH, within 1 ATR -> fires.
    assert strat.generate(_snap(close=100.7, opening_range_high=100.5, atr14=0.5), _ctx()) is not None
    # close far above ORH (> 1 ATR) -> chasing, rejected.
    assert strat.generate(_snap(close=102.0, opening_range_high=100.5, atr14=0.5), _ctx()) is None


def test_rs_momentum_requires_positive_rs():
    strat = RelativeStrengthMomentum()
    # Non-parabolic bar (close-open well within 1.5 ATR) so the setup is valid.
    good = _snap(open=100.7, close=101.0, atr14=0.5)
    assert strat.generate(good, _ctx()) is not None
    assert strat.generate(_snap(open=100.7, close=101.0, rs_vs_spy=-0.01), _ctx()) is None


def test_vwap_mean_reversion_fires_when_oversold_below_vwap():
    strat = VwapMeanReversion()
    snap = _snap(close=98.0, vwap=100.0, vwap_dev_atr=-2.0, rsi14=25.0,
                 low=97.8, session_low=97.5, atr14=0.5)
    cand = strat.generate(snap, _ctx())
    assert cand is not None
    assert cand.target_price <= snap.vwap + 1e-9  # target is VWAP or nearer


def test_vwap_mean_reversion_rejects_when_not_oversold():
    strat = VwapMeanReversion()
    assert strat.generate(_snap(vwap_dev_atr=-2.0, rsi14=45.0), _ctx()) is None


def test_scoring_is_bounded_0_100():
    card = score_signal(_snap(), risk_reward=2.0, session_bar_index=3)
    assert 0.0 <= card.total <= 100.0
    assert abs(sum(card.components.values()) - card.total) < 1e-6


def test_scoring_mean_reversion_rewards_below_vwap():
    below = score_signal(_snap(vwap_dev_atr=-2.0), risk_reward=1.5,
                         session_bar_index=3, mean_reversion=True)
    above = score_signal(_snap(vwap_dev_atr=0.5), risk_reward=1.5,
                        session_bar_index=3, mean_reversion=True)
    assert below.components["vwap_distance"] > above.components["vwap_distance"]
