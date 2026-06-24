"""Strategy 1: OPENING_RANGE_BREAKOUT.

Buys a confirmed close above the first-30-minute opening range, on expanding
volume, above VWAP, when SPY is not falling hard, and when the breakout is not
already over-extended.
"""
from __future__ import annotations

from datetime import time

import numpy as np

from app.domain import SignalCandidate
from app.enums import StrategyName
from app.indicators.intraday_indicators import IndicatorSnapshot
from app.strategies.base import IntradayStrategy, StrategyContext
from app.strategies.scoring import score_signal

MIN_RVOL = 1.5
MAX_BREAKOUT_ATR = 1.0  # breakout no more than 1 ATR above range high
MAX_RANGE_WIDTH_PCT = 0.05  # skip if opening range is extremely wide


class OpeningRangeBreakout(IntradayStrategy):
    name = StrategyName.OPENING_RANGE_BREAKOUT
    last_entry_time_et = time(14, 30)
    max_holding_bars = 8

    def __init__(self, enabled: bool = True, max_stop_pct: float = 0.025, tp_r: float = 1.5):
        super().__init__(enabled)
        self.max_stop_pct = max_stop_pct
        self.tp_r = tp_r

    def generate(self, snap: IndicatorSnapshot, ctx: StrategyContext) -> SignalCandidate | None:
        # Need the full opening range (after 10:00 ET) and not too late in the day.
        if not snap.opening_range_complete or ctx.session_bar_index < 2:
            return None
        if self._too_late(ctx):
            return None
        if any(np.isnan(x) for x in (snap.vwap, snap.atr14, snap.opening_range_high)):
            return None
        if snap.atr14 <= 0:
            return None

        # 1) confirmed CLOSE above the opening range high (not just an intrabar high)
        if snap.close <= snap.opening_range_high:
            return None
        # 5) above VWAP
        if snap.close <= snap.vwap:
            return None
        # 3/4) volume expansion
        if np.isnan(snap.relative_volume) or snap.relative_volume < MIN_RVOL:
            return None
        # 6) SPY not in a strong intraday downtrend
        if snap.spy_trend < -0.5:
            return None
        # 8) breakout not already > 1 ATR above the range high (chasing)
        if (snap.close - snap.opening_range_high) > MAX_BREAKOUT_ATR * snap.atr14:
            return None
        # 10) opening range not extreme
        if snap.close > 0 and (snap.opening_range_width / snap.close) > MAX_RANGE_WIDTH_PCT:
            return None

        # Stop: just below range high OR 1 ATR below entry — take the closer (tighter
        # but not random) one, then clamp.
        stop_candidate = max(snap.opening_range_high, snap.close - snap.atr14)
        stop = self.shape_long_stop(snap.close, stop_candidate, snap.atr14, self.max_stop_pct)
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
                "invalidation": "close_below_vwap_or_back_into_range",
                "opening_range_high": snap.opening_range_high,
                "tp_r": self.tp_r,
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
