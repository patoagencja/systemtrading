"""Liquidity / tradability filters for universe construction.

Applies the configured price, volume, dollar-volume, history-length and
asset-type gates (see ``app.config.settings``) to per-symbol metrics and
returns ranked candidates. Penny stocks (price < ``settings.min_price_usd``,
default 5 USD) are excluded; only common stock and ETFs are eligible.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.enums import AssetType
from app.logging_config import get_logger

logger = get_logger(__name__)

_ELIGIBLE_TYPES = {AssetType.COMMON_STOCK.value, AssetType.ETF.value}


@dataclass
class LiquidityMetrics:
    """Computed liquidity metrics for one symbol over the lookback window."""

    symbol: str
    price: float
    adv: float  # average daily share volume
    dollar_volume: float  # average daily dollar volume
    history_days: int
    asset_type: str
    atr_daily: float = 0.0


@dataclass
class LiquidityVerdict:
    """Pass/fail verdict for one symbol, with the failing reasons."""

    symbol: str
    passed: bool
    reasons: list[str]
    metrics: LiquidityMetrics


def evaluate(metrics: LiquidityMetrics) -> LiquidityVerdict:
    """Apply all liquidity gates to a single symbol's metrics."""
    reasons: list[str] = []

    if metrics.asset_type not in _ELIGIBLE_TYPES:
        reasons.append(f"asset_type {metrics.asset_type!r} not in {_ELIGIBLE_TYPES}")

    if metrics.price < settings.min_price_usd:
        reasons.append(f"price {metrics.price:.2f} < min {settings.min_price_usd}")
    if metrics.price > settings.max_price_usd:
        reasons.append(f"price {metrics.price:.2f} > max {settings.max_price_usd}")

    if metrics.adv < settings.min_avg_daily_volume:
        reasons.append(f"ADV {metrics.adv:.0f} < min {settings.min_avg_daily_volume:.0f}")
    if metrics.dollar_volume < settings.min_avg_dollar_volume_usd:
        reasons.append(
            f"dollar_vol {metrics.dollar_volume:.0f} < min "
            f"{settings.min_avg_dollar_volume_usd:.0f}"
        )

    if metrics.history_days < settings.min_history_days:
        reasons.append(
            f"history {metrics.history_days}d < min {settings.min_history_days}d"
        )

    return LiquidityVerdict(metrics.symbol, not reasons, reasons, metrics)


def filter_candidates(
    metrics: list[LiquidityMetrics],
) -> list[LiquidityVerdict]:
    """Evaluate and rank passing candidates by average dollar volume desc.

    Returns only the passing verdicts, ranked. Ranking key: dollar volume
    (primary), then share ADV, then daily ATR (volatility) as a tie-breaker.
    """
    verdicts = [evaluate(m) for m in metrics]
    passing = [v for v in verdicts if v.passed]
    passing.sort(
        key=lambda v: (v.metrics.dollar_volume, v.metrics.adv, v.metrics.atr_daily),
        reverse=True,
    )
    logger.info(
        "liquidity_filter: %d/%d symbols passed gates.", len(passing), len(metrics)
    )
    return passing
