"""Market scanner — pulls the latest bars for the universe and validates them.

It enforces the global data-quality gates: nothing downstream trades on stale,
incomplete, or obviously broken data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.data.data_validator import validate_bars
from app.data.provider_base import MarketDataProvider
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ScanResult:
    bars: dict[str, pd.DataFrame]
    issues: dict[str, list[str]] = field(default_factory=dict)
    fetched_at: datetime | None = None

    @property
    def healthy_symbols(self) -> list[str]:
        return [s for s in self.bars if s not in self.issues]


class MarketScanner:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    def fetch_latest(
        self, symbols: list[str], *, lookback: int = 60, now: datetime | None = None
    ) -> ScanResult:
        now = now or datetime.now(UTC)
        raw = self.provider.get_latest_bars(symbols, lookback=lookback, timeframe=settings.bar_interval)
        issues: dict[str, list[str]] = {}
        clean: dict[str, pd.DataFrame] = {}
        for sym, df in raw.items():
            vr = validate_bars(df, sym, max_staleness_seconds=settings.max_data_staleness_seconds,
                               now=now)
            if not vr.ok:
                issues[sym] = vr.reasons
                logger.warning("Data issue for %s: %s", sym, ", ".join(vr.reasons))
            else:
                clean[sym] = df
        return ScanResult(bars=clean, issues=issues, fetched_at=now)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=False)
def _fetch_nbp_usdpln() -> float:
    url = "https://api.nbp.pl/api/exchangerates/rates/a/usd/?format=json"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return float(resp.json()["rates"][0]["mid"])


def get_usdpln() -> float:
    """Daily USD/PLN. Uses the free NBP reference rate; falls back to the env value.

    Never invents a rate silently: a fallback is logged so it is visible.
    """
    try:
        rate = _fetch_nbp_usdpln()
        if rate and rate > 0:
            return rate
    except Exception as exc:  # noqa: BLE001
        logger.warning("USD/PLN fetch failed (%s); using fallback %.4f", exc, settings.fallback_usdpln)
    return settings.fallback_usdpln
