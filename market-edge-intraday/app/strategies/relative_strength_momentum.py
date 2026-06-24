"""Strategy 3: RELATIVE_STRENGTH_MOMENTUM.

Buys names that are clearly outperforming both SPY and their sector, while the
broad market is at least neutral. Manages winners with a move to break-even at
1R and a trailing stop thereafter (handled by the position manager via metadata).
"""
from __future__ import annotations

from datetime import time

import numpy as np

from app.domain import SignalCandidate
from app.enums import StrategyName
from app.indicators.intraday_indicators import IndicatorSnapshot
from app.strategies.base import IntradayStrategy, StrategyContext
from app.strategies.scoring import score_signal

MIN_RVOL = 1.2
MAX_VWAP_EXT_ATR = 2.0
MAX_STOP_PCT = 0.025


class RelativeStrengthMomentum(IntradayStrategy):
    name = StrategyName.RELATIVE_STRENGTH_MOMENTUM
    last_entry_time_et = time(14, 45)
    max_holding_bars = 10

    def __init__(self, enabled: bool = True, tp_r: float = 2.0):
        super().__init__(enabled)
        self.tp_r = tp_r

    def generate(self, snap: IndicatorSnapshot, ctx: StrategyContext) -> SignalCandidate | None:
        if self._too_late(ctx):
            return None
        if any(np.isnan(x) for x in (snap.vwap, snap.atr14, snap.ema9, snap.ema20)):
            return None
        if snap.atr14 <= 0:
            return None

        # 1) above VWAP
        if snap.close <= snap.vwap:
            return None
        # 2) positive intraday return
        if np.isnan(snap.return_from_open) or snap.return_from_open <= 0:
            return None
        # 3) SPY neutral or positive
        if snap.spy_trend < -0.1:
            return None
        # 4) positive RS vs SPY
        if np.isnan(snap.rs_vs_spy) or snap.rs_vs_spy <= 0:
            return None
        # 5) positive RS vs sector (require when available)
        if not np.isnan(snap.rs_vs_sector) and snap.rs_vs_sector <= 0:
            return None
        # 6) relative volume
        if np.isnan(snap.relative_volume) or snap.relative_volume < MIN_RVOL:
            return None
        # 7) trend: EMA9 > EMA20
        if snap.ema9 <= snap.ema20:
            return None
        # 8) not over-extended from VWAP
        if not np.isnan(snap.vwap_dev_atr) and snap.vwap_dev_atr > MAX_VWAP_EXT_ATR:
            return None
        # 9) last candle not a parabolic blow-off
        if (snap.close - snap.open) > 1.5 * snap.atr14:
            return None

        # Stop below EMA20 or 1.2 ATR below entry — the closer one, then clamp.
        stop_candidate = max(snap.ema20, snap.close - 1.2 * snap.atr14)
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
                "trail_after_r": 1.5,
                "trail_atr": 1.0,
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
