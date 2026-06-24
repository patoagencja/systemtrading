"""Massive (Polygon-style) market-data provider (real REST implementation).

Calls a Polygon-compatible aggregates API at ``settings.massive_base_url``.
Returns split/dividend-adjusted bars honoring the strict
:class:`~app.data.provider_base.MarketDataProvider` contract. A missing API key
raises a clear ``RuntimeError`` — we never fabricate data.

Aggregates endpoint:
    /v2/aggs/ticker/{symbol}/range/15/minute/{from}/{to}
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.data.provider_base import MarketDataProvider, empty_bars, normalize_bars
from app.data.session_calendar import is_trading_day, now_et, session_bounds_utc
from app.enums import AssetType, MarketStatus
from app.logging_config import get_logger

logger = get_logger(__name__)

# (multiplier, timespan) per timeframe string.
_TIMEFRAME_MAP: dict[str, tuple[int, str]] = {
    "1Min": (1, "minute"),
    "5Min": (5, "minute"),
    "15Min": (15, "minute"),
    "30Min": (30, "minute"),
    "1H": (1, "hour"),
    "1Hour": (1, "hour"),
    "1Day": (1, "day"),
    "1D": (1, "day"),
}

_RETRYABLE = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
)


class _RetryableStatus(requests.exceptions.HTTPError):
    """Raised for 5xx / 429 responses so tenacity retries them."""


class MassiveProvider(MarketDataProvider):
    """Market-data reads against a Polygon-compatible aggregates API."""

    name = "massive"

    def __init__(self) -> None:
        if not settings.massive_api_key:
            raise RuntimeError(
                "Massive API key missing. Set MASSIVE_API_KEY in your .env to "
                "use the massive provider. This system never fabricates data."
            )
        self._base = settings.massive_base_url.rstrip("/")
        self._key = settings.massive_api_key
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------ HTTP
    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        reraise=True,
    )
    def _get(self, url: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["apiKey"] = self._key
        resp = self._session.get(url, params=params, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _RetryableStatus(
                f"Massive {resp.status_code} for {url}: {resp.text[:200]}",
                response=resp,
            )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _ymd(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%d")

    @staticmethod
    def _parse_results(results: list[dict]) -> pd.DataFrame:
        if not results:
            return empty_bars()
        df = pd.DataFrame(results)
        df = df.rename(
            columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
        )
        # Polygon "t" is epoch milliseconds at the bar's *start*.
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
        return normalize_bars(df)

    def _fetch_symbol(
        self, symbol: str, start: datetime, end: datetime, timeframe: str
    ) -> pd.DataFrame:
        mult, span = _TIMEFRAME_MAP.get(timeframe, (15, "minute"))
        url = (
            f"{self._base}/v2/aggs/ticker/{symbol}/range/{mult}/{span}/"
            f"{self._ymd(start)}/{self._ymd(end)}"
        )
        results: list[dict] = []
        params: dict[str, object] = {"adjusted": "true", "sort": "asc", "limit": 50000}
        next_url: str | None = url
        while next_url:
            try:
                payload = self._get(next_url, params if next_url == url else None)
            except Exception as exc:
                logger.error("Massive bars failed for %s: %s", symbol, exc)
                break
            results.extend(payload.get("results") or [])
            next_url = payload.get("next_url")
            params = {}  # next_url already carries query params (except apiKey)
        return self._parse_results(results)

    # -------------------------------------------------------------- contract
    def get_historical_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            out[sym] = self._fetch_symbol(sym, start, end, timeframe)
        return out

    def get_latest_bars(
        self,
        symbols: list[str],
        lookback: int = 50,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        end = datetime.now(tz=UTC)
        days = max(5, (lookback // 13) + 5)
        start = end - pd.Timedelta(days=days)
        bars = self.get_historical_bars(symbols, start, end, timeframe)
        return {sym: df.tail(lookback) for sym, df in bars.items()}

    def get_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sym in symbols:
            url = f"{self._base}/v2/snapshot/locale/us/markets/stocks/tickers/{sym}"
            try:
                payload = self._get(url)
            except Exception as exc:
                logger.error("Massive snapshot failed for %s: %s", sym, exc)
                continue
            ticker = payload.get("ticker") or {}
            last_trade = ticker.get("lastTrade") or {}
            last_quote = ticker.get("lastQuote") or {}
            day = ticker.get("day") or {}
            out[sym] = {
                "symbol": sym,
                "price": last_trade.get("p"),
                "bid": last_quote.get("p") or last_quote.get("bp"),
                "ask": last_quote.get("P") or last_quote.get("ap"),
                "volume": day.get("v"),
                "timestamp": last_trade.get("t"),
            }
        return out

    def get_assets(self) -> list[dict]:
        url = f"{self._base}/v3/reference/tickers"
        params: dict[str, object] = {
            "market": "stocks",
            "active": "true",
            "limit": 1000,
        }
        assets: list[dict] = []
        next_url: str | None = url
        guard = 0
        while next_url and guard < 50:
            guard += 1
            try:
                payload = self._get(next_url, params if next_url == url else None)
            except Exception as exc:
                logger.error("Massive get_assets failed: %s", exc)
                break
            for r in payload.get("results") or []:
                kind = (r.get("type") or "").upper()
                if kind == "ETF":
                    asset_type = AssetType.ETF
                elif kind in ("CS", "ADRC", ""):
                    asset_type = AssetType.COMMON_STOCK
                else:
                    continue
                assets.append(
                    {
                        "symbol": r.get("ticker"),
                        "name": r.get("name"),
                        "exchange": r.get("primary_exchange"),
                        "asset_type": asset_type.value,
                        "sector": None,
                    }
                )
            next_url = payload.get("next_url")
            params = {}
        return assets

    def get_market_status(self) -> MarketStatus:
        url = f"{self._base}/v1/marketstatus/now"
        try:
            payload = self._get(url)
            market = (payload.get("market") or "").lower()
            if market == "open":
                return MarketStatus.OPEN
            if market == "extended-hours":
                return MarketStatus.AFTERHOURS
            if market == "closed":
                return MarketStatus.CLOSED
        except Exception as exc:
            logger.warning("Massive marketstatus failed (%s); using calendar.", exc)
        now = now_et()
        if not is_trading_day(now):
            return MarketStatus.CLOSED
        bounds = session_bounds_utc(now)
        if bounds is None:
            return MarketStatus.CLOSED
        open_utc, close_utc = bounds
        now_utc = now.astimezone(UTC)
        if now_utc < open_utc:
            return MarketStatus.PREMARKET
        if now_utc >= close_utc:
            return MarketStatus.AFTERHOURS
        return MarketStatus.OPEN
