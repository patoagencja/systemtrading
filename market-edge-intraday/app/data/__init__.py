"""Data provider package: factory + public exports.

Use :func:`get_provider` to obtain the configured market-data provider. The
provider is chosen from ``settings.market_data_provider`` unless an explicit
name is supplied.
"""
from __future__ import annotations

from app.config import settings
from app.data.provider_base import (
    BAR_COLUMNS,
    MarketDataProvider,
    empty_bars,
    normalize_bars,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

__all__ = [
    "MarketDataProvider",
    "BAR_COLUMNS",
    "empty_bars",
    "normalize_bars",
    "get_provider",
]


def get_provider(name: str | None = None) -> MarketDataProvider:
    """Return a market-data provider instance.

    ``name`` is one of ``"alpaca"``, ``"massive"``, ``"fixture"``. When ``None``
    the value of ``settings.market_data_provider`` is used. Unknown names raise
    ``ValueError``. Live providers raise ``RuntimeError`` if credentials are
    missing — they never fabricate data.
    """
    resolved = (name or settings.market_data_provider or "alpaca").lower()
    if resolved == "alpaca":
        from app.data.alpaca_provider import AlpacaProvider

        return AlpacaProvider()
    if resolved == "massive":
        from app.data.massive_provider import MassiveProvider

        return MassiveProvider()
    if resolved == "fixture":
        from app.data.fixture_provider import FixtureProvider

        return FixtureProvider()
    raise ValueError(
        f"Unknown market_data_provider {resolved!r}; "
        "expected one of: alpaca, massive, fixture."
    )
