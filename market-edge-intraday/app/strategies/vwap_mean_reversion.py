"""Strategy 2: VWAP_MEAN_REVERSION.

Fades an over-extension below VWAP in a liquid name — but only with signs of
stabilisation and only when SPY is not crashing. It is deliberately selective:
a low RSI alone is never enough.
"""
from __future__ import annotations

from datetime import time

import numpy as np

from app.domain import SignalCandidate
from app.enums import StrategyName
from app.indicators.intraday_indicators import IndicatorSnapshot
from app.strategies.base import IntradayStrategy, StrategyContext
from app.strategies.scoring import score_signal

MIN_DEV_ATR = 1.5  # at least 1.5 ATR below VWAP
MAX_RSI = 30.0
MAX_STOP_PCT = 0.02


class VwapMeanReversion(IntradayStrategy):
    name = StrategyName.VWAP_MEAN_REVERSION
    last_entry_time_et = time(15, 0)
    max_holding_bars = 6

    def __init__(self, enabled: bool = True, tp_r: float = 1.5):
        super().__init__(enabled)
        self.tp_r = tp_r

    def generate(self, snap: IndicatorSnapshot, ctx: StrategyContext) -> SignalCandidate | None:
        # 9) not in the first 30 minutes; 10) not after 15:00
        if ctx.session_bar_index < 2 or self._too_late(ctx):
            return None
        if any(np.isnan(x) for x in (snap.vwap, snap.atr14, snap.vwap_dev_atr, snap.rsi14)):
            return None
        if snap.atr14 <= 0:
            return None

        # 1/2) clearly below VWAP by at least MIN_DEV_ATR
        if snap.vwap_dev_atr > -MIN_DEV_ATR:
            return None
        # 3) oversold
        if snap.rsi14 >= MAX_RSI:
            return None
        # 5) stabilisation: close off the low of the just-closed bar
        if snap.close <= snap.low:
            return None
        # 6) not a cascade of accelerating down-volume (proxy: not an extreme rvol dump
        #    on a red bar)
        if snap.bar_return < 0 and not np.isnan(snap.relative_volume) and snap.relative_volume > 3.0:
            return None
        # 7) SPY not in a strong downtrend
        if snap.spy_trend < -0.5:
            return None
        # 8) not bleaking with strong negative sector momentum
        if not np.isnan(snap.rs_vs_sector) and snap.rs_vs_sector < -0.015:
            return None

        # Stop below the session low, capped at 2%.
        stop_candidate = snap.session_low - 0.1 * snap.atr14
        stop = self.shape_long_stop(snap.close, stop_candidate, snap.atr14, MAX_STOP_PCT)
        risk = snap.close - stop
        if risk <= 0:
            return None
        # Target: the nearer of VWAP or 1.5R.
        target_rr = snap.close + self.tp_r * risk
        target = min(snap.vwap, target_rr) if snap.vwap > snap.close else target_rr
        if target <= snap.close:
            return None

        cand = self._long(
            snap,
            ctx,
            self.name,
            stop_price=stop,
            target_price=target,
            metadata={
                "max_holding_bars": self.max_holding_bars,
                "target_basis": "vwap_or_1.5R",
                "tp_r": self.tp_r,
            },
        )
        card = score_signal(
            snap,
            risk_reward=cand.risk_reward,
            session_bar_index=ctx.session_bar_index,
            mean_reversion=True,
        )
        cand.score = card.total
        cand.metadata["score_components"] = card.as_dict()
        return cand
