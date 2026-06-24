"""Abstract market-data provider contract.

Every concrete provider (Alpaca, Massive/Polygon, Fixture) must implement
:class:`MarketDataProvider`. The DataFrame contract is strict and shared:

Each per-symbol DataFrame returned by a provider MUST:
    * be indexed by timezone-aware UTC timestamps (the bar's **open** time),
    * have columns exactly ``["open", "high", "low", "close", "volume"]``,
    * be sorted ascending by timestamp,
    * contain only **completed** bars (no partial in-progress bar),
    * be empty (``pandas.DataFrame`` with the right columns) for symbols that
      have no data — providers never raise for a single missing symbol.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from app.enums import MarketStatus

#: The exact, ordered OHLCV columns every provider DataFrame must expose.
BAR_COLUMNS: list[str] = ["open", "high", "low", "close", "volume"]


def empty_bars() -> pd.DataFrame:
    """Return a correctly-shaped empty bars DataFrame (UTC index, OHLCV cols)."""
    idx = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    return pd.DataFrame(columns=BAR_COLUMNS, index=idx, dtype="float64")


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a raw OHLCV frame to the strict provider contract.

    Ensures UTC tz-aware index, exact column set/order, ascending sort and
    de-duplicated timestamps (keeping the last observation per timestamp).
    """
    if df is None or df.empty:
        return empty_bars()
    out = df.copy()
    # Index -> tz-aware UTC.
    idx = pd.DatetimeIndex(out.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out.index = idx
    out.index.name = "timestamp"
    # Columns -> exact set/order.
    for col in BAR_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[BAR_COLUMNS].astype("float64")
    out = out[~out.index.duplicated(keep="last")]
    out = out.sort_index()
    return out


class MarketDataProvider(ABC):
    """Read-only market-data source. Implementations never place orders."""

    #: Short provider identifier (e.g. ``"alpaca"``, ``"massive"``, ``"fixture"``).
    name: str = "base"

    @abstractmethod
    def get_historical_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        """Historical OHLCV bars for ``symbols`` between ``start`` and ``end``.

        Returns a mapping ``symbol -> DataFrame`` honoring the module contract.
        """

    @abstractmethod
    def get_latest_bars(
        self,
        symbols: list[str],
        lookback: int = 50,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        """The most recent ``lookback`` completed bars per symbol."""

    @abstractmethod
    def get_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        """Latest price / bid / ask / volume per symbol (best-effort)."""

    @abstractmethod
    def get_assets(self) -> list[dict]:
        """Tradable assets.

        Each dict has keys: ``symbol``, ``name``, ``exchange``, ``asset_type``,
        ``sector``.
        """

    @abstractmethod
    def get_market_status(self) -> MarketStatus:
        """Current US-equity market status."""
