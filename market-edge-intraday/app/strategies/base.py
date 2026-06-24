"""Strategy base class and shared context.

A strategy is a pure function of a completed-bar :class:`IndicatorSnapshot`
plus a light :class:`StrategyContext` (clock + benchmark state). It returns at
most one :class:`SignalCandidate`. Strategies NEVER look at the forming bar or
any future bar — they only see indicators computed on closed bars.

Entry always happens on the *open of the next bar* (handled by the broker), so
the strategy only proposes; it does not execute.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time

from app.config import settings
from app.domain import SignalCandidate
from app.enums import Side, StrategyName
from app.indicators.intraday_indicators import IndicatorSnapshot


@dataclass
class StrategyContext:
    """Everything a strategy needs beyond the per-symbol indicator snapshot."""

    now_utc: datetime
    now_et: time  # current wall-clock in America/New_York
    session_bar_index: int  # 0-based index of the just-closed bar within session
    bars_remaining: int  # completed bars left until force-close window
    spy_trend: float  # [-1, 1] intraday SPY trend (also on each snapshot)
    cost_scenario_round_trip_pct: float = 0.0012  # informational, for R/R checks
    extra: dict = field(default_factory=dict)


class IntradayStrategy(ABC):
    """Base class for all intraday strategies."""

    name: StrategyName
    #: minutes after which no *new* entries are allowed for this strategy
    last_entry_time_et: time = time(14, 30)
    #: max number of 15-minute bars to hold before a time-stop exit
    max_holding_bars: int = 8

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    # ------------------------------------------------------------------ helpers
    def _too_late(self, ctx: StrategyContext) -> bool:
        return ctx.now_et >= self.last_entry_time_et

    def is_enabled(self) -> bool:
        return self.enabled and self.name in settings.enabled_strategy_set

    # ------------------------------------------------------------------ contract
    @abstractmethod
    def generate(
        self, snap: IndicatorSnapshot, ctx: StrategyContext
    ) -> SignalCandidate | None:
        """Return a scored long candidate, or None if no setup is present.

        Implementations must:
        * use only ``snap`` (computed on closed bars) and ``ctx``;
        * set ``reference_price`` to the close of bar T (snap.close);
        * set a sensible ``stop_price`` and ``target_price``;
        * compute and attach ``score`` in [0, 100];
        * be long-only in V1 (Side.LONG).
        """
        raise NotImplementedError

    # convenience for subclasses
    @staticmethod
    def shape_long_stop(
        close: float,
        candidate_stop: float,
        atr: float,
        max_stop_pct: float,
        min_atr_frac: float = 0.3,
    ) -> float:
        """Clamp a long stop so it is neither too wide nor randomly too tight.

        * never further than ``max_stop_pct`` below entry;
        * never closer than ``min_atr_frac`` * ATR below entry (avoids noise
          knockouts) — but still always below entry.
        """
        stop = candidate_stop
        max_dist = close * max_stop_pct
        min_dist = max(min_atr_frac * atr, close * 0.002)
        dist = close - stop
        if dist > max_dist:
            stop = close - max_dist
        elif dist < min_dist:
            stop = close - min_dist
        return stop

    @staticmethod
    def _long(
        snap: IndicatorSnapshot,
        ctx: StrategyContext,
        name: StrategyName,
        stop_price: float,
        target_price: float,
        metadata: dict | None = None,
    ) -> SignalCandidate:
        return SignalCandidate(
            symbol=snap.symbol,
            strategy=name,
            side=Side.LONG,
            signal_time=ctx.now_utc,
            reference_price=snap.close,
            stop_price=stop_price,
            target_price=target_price,
            metadata=metadata or {},
        )
