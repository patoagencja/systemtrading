"""Signal scoring (0-100). Deterministic, documented, NOT a random point sum.

Each signal is scored across ten dimensions. Every dimension produces a value
in [0, 1]; it is then multiplied by its weight (weights sum to 100) and summed.
The maximum achievable score is therefore 100. A signal below
``settings.min_signal_score`` (default 75) is rejected.

Dimensions and default weights
------------------------------
| Dimension              | Weight | Rewards                                   |
|------------------------|:------:|-------------------------------------------|
| liquidity              |   12   | high dollar volume / ADV                  |
| spread                 |    8   | tight (estimated) spread                  |
| relative_volume        |   14   | current bar volume >> 20-bar average      |
| spy_trend_alignment    |   12   | SPY trend agrees with the trade direction |
| relative_strength      |   12   | outperformance vs SPY and sector          |
| candle_quality         |   10   | strong close location / clean bar         |
| vwap_distance          |    8   | strategy-appropriate distance from VWAP   |
| reward_risk            |   12   | higher reward/risk ratio                  |
| time_of_day            |    6   | earlier in the allowed window             |
| volatility             |    6   | enough range to move, not chaotic         |

Points are only ever *added* (each dimension contributes >= 0). A dimension
that is unfavourable contributes 0 for that dimension, which is how points are
effectively "subtracted" relative to the 100 maximum.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.indicators.intraday_indicators import IndicatorSnapshot

DEFAULT_WEIGHTS: dict[str, float] = {
    "liquidity": 12.0,
    "spread": 8.0,
    "relative_volume": 14.0,
    "spy_trend_alignment": 12.0,
    "relative_strength": 12.0,
    "candle_quality": 10.0,
    "vwap_distance": 8.0,
    "reward_risk": 12.0,
    "time_of_day": 6.0,
    "volatility": 6.0,
}

# Allowed entry window for time-of-day scoring (regular session, minutes from open).
_SESSION_MINUTES = 6.5 * 60


def _clip01(x: float) -> float:
    if x is None or np.isnan(x):
        return 0.0
    return float(max(0.0, min(1.0, x)))


@dataclass
class ScoreCard:
    total: float
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"total": round(self.total, 2), **{k: round(v, 2) for k, v in self.components.items()}}


def _liquidity_score(snap: IndicatorSnapshot) -> float:
    # Dollar volume of the bar relative to a healthy 25M+/day -> 15min target.
    dv = snap.close * snap.volume
    # A single 15-min bar trading >$2M is plenty liquid for our position sizes.
    return _clip01(dv / 2_000_000.0)


def _spread_score(snap: IndicatorSnapshot) -> float:
    # Estimated spread bps from metadata if present; default to a tight assumption.
    spread_bps = snap.extra.get("spread_bps")
    if spread_bps is None:
        return 0.7  # neutral-good when unknown
    # 1 bp -> 1.0, 10 bps -> ~0.0
    return _clip01(1.0 - (spread_bps - 1.0) / 9.0)


def _rvol_score(snap: IndicatorSnapshot, target: float = 2.0) -> float:
    if np.isnan(snap.relative_volume):
        return 0.0
    return _clip01(snap.relative_volume / target)


def _spy_alignment_long(snap: IndicatorSnapshot) -> float:
    # Long trades like neutral-to-positive SPY. trend in [-1,1].
    return _clip01((snap.spy_trend + 0.5) / 1.0)


def _relative_strength_score(snap: IndicatorSnapshot) -> float:
    rs = 0.0
    n = 0
    if not np.isnan(snap.rs_vs_spy):
        rs += _clip01(snap.rs_vs_spy / 0.01)  # +1% RS -> 1.0
        n += 1
    if not np.isnan(snap.rs_vs_sector):
        rs += _clip01(snap.rs_vs_sector / 0.01)
        n += 1
    return rs / n if n else 0.5


def _candle_quality_score(snap: IndicatorSnapshot) -> float:
    # Close position in bar range; reward closes in the upper part for longs.
    rng = snap.high - snap.low
    if rng <= 0:
        return 0.3
    pos = (snap.close - snap.low) / rng
    return _clip01(pos)


def _vwap_distance_score_momentum(snap: IndicatorSnapshot) -> float:
    # Momentum: above VWAP good, but not over-extended (>2 ATR) -> penalise.
    if np.isnan(snap.vwap_dev_atr):
        return 0.5
    d = snap.vwap_dev_atr
    if d < 0:
        return 0.2
    # peak score around +0.5..+1.5 ATR, decaying beyond 2 ATR
    if d <= 1.5:
        return _clip01(0.5 + d / 3.0)
    return _clip01(1.0 - (d - 1.5) / 1.5)


def _vwap_distance_score_meanrev(snap: IndicatorSnapshot) -> float:
    # Mean reversion: the further BELOW VWAP, the better (oversold).
    if np.isnan(snap.vwap_dev_atr):
        return 0.5
    d = -snap.vwap_dev_atr  # positive when below vwap
    return _clip01(d / 3.0)


def _reward_risk_score(rr: float, target: float = 2.0) -> float:
    return _clip01(rr / target)


def _time_of_day_score(session_bar_index: int) -> float:
    # Earlier entries (more time for the trade to work) score higher.
    total_bars = _SESSION_MINUTES / 15.0
    return _clip01(1.0 - session_bar_index / total_bars)


def _volatility_score(snap: IndicatorSnapshot) -> float:
    # Want some ATR relative to price, but not extreme.
    if snap.close <= 0 or np.isnan(snap.atr14):
        return 0.3
    atr_pct = snap.atr14 / snap.close
    # sweet spot ~0.3%-1.5% per 15-min bar
    if atr_pct < 0.001:
        return 0.2
    if atr_pct <= 0.015:
        return 1.0
    return _clip01(1.0 - (atr_pct - 0.015) / 0.02)


def score_signal(
    snap: IndicatorSnapshot,
    *,
    risk_reward: float,
    session_bar_index: int,
    mean_reversion: bool = False,
    weights: dict[str, float] | None = None,
) -> ScoreCard:
    """Compute a 0-100 score card for a candidate.

    ``mean_reversion`` flips the VWAP-distance dimension so far-below-VWAP setups
    score highly (used by VWAP_MEAN_REVERSION); momentum strategies keep the
    default orientation.
    """
    w = weights or DEFAULT_WEIGHTS
    vwap_fn = _vwap_distance_score_meanrev if mean_reversion else _vwap_distance_score_momentum
    comps01 = {
        "liquidity": _liquidity_score(snap),
        "spread": _spread_score(snap),
        "relative_volume": _rvol_score(snap),
        "spy_trend_alignment": _spy_alignment_long(snap),
        "relative_strength": _relative_strength_score(snap),
        "candle_quality": _candle_quality_score(snap),
        "vwap_distance": vwap_fn(snap),
        "reward_risk": _reward_risk_score(risk_reward),
        "time_of_day": _time_of_day_score(session_bar_index),
        "volatility": _volatility_score(snap),
    }
    components = {k: comps01[k] * w.get(k, 0.0) for k in comps01}
    total = float(sum(components.values()))
    return ScoreCard(total=total, components=components)
