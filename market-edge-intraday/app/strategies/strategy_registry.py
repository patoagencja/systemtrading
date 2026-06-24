"""Registry of intraday strategies.

Each strategy is independent: its own config, scoring and statistics. The
registry simply instantiates the enabled set and offers convenience iteration.
A strategy can be toggled via ``settings.enabled_strategies``.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.config import settings
from app.domain import SignalCandidate
from app.enums import StrategyName
from app.indicators.intraday_indicators import IndicatorSnapshot
from app.strategies.base import IntradayStrategy, StrategyContext
from app.strategies.opening_range_breakout import OpeningRangeBreakout
from app.strategies.relative_strength_momentum import RelativeStrengthMomentum
from app.strategies.volume_expansion import VolumeExpansionMomentum
from app.strategies.vwap_mean_reversion import VwapMeanReversion

_FACTORIES = {
    StrategyName.OPENING_RANGE_BREAKOUT: OpeningRangeBreakout,
    StrategyName.VWAP_MEAN_REVERSION: VwapMeanReversion,
    StrategyName.RELATIVE_STRENGTH_MOMENTUM: RelativeStrengthMomentum,
    StrategyName.VOLUME_EXPANSION_MOMENTUM: VolumeExpansionMomentum,
}


def build_strategies(
    names: Iterable[StrategyName] | None = None,
    *,
    orb_max_stop_pct: float | None = None,
) -> list[IntradayStrategy]:
    """Instantiate strategies. Defaults to the enabled set from settings."""
    selected = set(names) if names is not None else settings.enabled_strategy_set
    out: list[IntradayStrategy] = []
    for name, factory in _FACTORIES.items():
        if name not in selected:
            continue
        if name == StrategyName.OPENING_RANGE_BREAKOUT:
            out.append(
                factory(max_stop_pct=orb_max_stop_pct or settings.orb_max_stop_pct)
            )
        else:
            out.append(factory())
    return out


def generate_all(
    strategies: list[IntradayStrategy],
    snap: IndicatorSnapshot,
    ctx: StrategyContext,
) -> list[SignalCandidate]:
    """Run every enabled strategy on one snapshot; return all candidates.

    De-duplication across strategies (one position per ticker, highest score
    wins) is the scanner/risk layer's responsibility — strategies stay
    independent here.
    """
    candidates: list[SignalCandidate] = []
    for strat in strategies:
        if not strat.is_enabled():
            continue
        cand = strat.generate(snap, ctx)
        if cand is not None:
            candidates.append(cand)
    return candidates


ALL_STRATEGY_NAMES = list(_FACTORIES.keys())
