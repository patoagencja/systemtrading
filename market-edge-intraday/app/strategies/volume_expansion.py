"""Strategy 4: VOLUME_EXPANSION_MOMENTUM.

Looks for a sudden, well-above-average volume bar that closes strong, above
VWAP, in an up-trend — then enters on the *next* bar (confirmation), never on
the very top of the first spike (entry-on-T+1 is enforced by the broker).
"""
from __future__ import annotations

from datetime import time

import numpy as np

from app.domain import SignalCandidate
from app.enums import StrategyName
from app.indicators.intraday_indicators import IndicatorSnapshot
from app.strategies.base import IntradayStrategy, StrategyContext
from app.strategies.scoring import score_signal

MIN_RVOL = 2.5
MAX_VWAP_EXT_ATR = 2.0
MAX_STOP_PCT = 0.025
MAX_SPREAD_BPS = 15.0


class VolumeExpansionMomentum(IntradayStrategy):
    name = StrategyName.VOLUME_EXPANSION_MOMENTUM
    last_entry_time_et = time(14, 45)
    max_holding_bars = 6

    def __init__(self, enabled: bool = True, tp_r: float = 1.5):
        super().__init__(enabled)
        self.tp_r = tp_r

    def generate(self, snap: IndicatorSnapshot, ctx: StrategyContext) -> SignalCandidate | None:
        if self._too_late(ctx):
            return None
        if any(np.isnan(x) for x in (snap.vwap, snap.atr14, snap.ema9, snap.ema20)):
            return None
        if snap.atr14 <= 0:
            return None

        # 1) volume expansion >= 2.5x average
        if np.isnan(snap.relative_volume) or snap.relative_volume < MIN_RVOL:
            return None
        # 2) close in the upper 25% of the bar range
        if not snap.bar_close_in_top_quartile:
            return None
        # 3) positive bar return
        if np.isnan(snap.bar_return) or snap.bar_return <= 0:
            return None
        # 4) above VWAP
        if snap.close <= snap.vwap:
            return None
        # 5) EMA9 > EMA20
        if snap.ema9 <= snap.ema20:
            return None
        # 6) not more than 2 ATR above VWAP
        if not np.isnan(snap.vwap_dev_atr) and snap.vwap_dev_atr > MAX_VWAP_EXT_ATR:
            return None
        # 8) SPY/sector not in a sharp drop
        if snap.spy_trend < -0.5:
            return None
        # 9) not an extreme spread
        spread_bps = snap.extra.get("spread_bps")
        if spread_bps is not None and spread_bps > MAX_SPREAD_BPS:
            return None

        # Stop below the low of the expansion candle, capped at 2.5%.
        stop_candidate = snap.low - 0.05 * snap.atr14
        stop = self.shape_long_stop(snap.close, stop_candidate, snap.atr14, MAX_STOP_PCT)
        risk = snap.close - stop
        if risk <= 0:
            return None
        target = snap.close + self.tp_r * risk

        cand = self._long(
            snap,
            ctx,
            self.name,
            stop_price=stop,
            target_price=target,
            metadata={
                "max_holding_bars": self.max_holding_bars,
                "tp_r": self.tp_r,
                "breakeven_at_r": 1.0,
                "expansion_low": snap.low,
            },
        )
        card = score_signal(
            snap,
            risk_reward=cand.risk_reward,
            session_bar_index=ctx.session_bar_index,
            mean_reversion=False,
        )
        cand.score = card.total
        cand.metadata["score_components"] = card.as_dict()
        return cand
