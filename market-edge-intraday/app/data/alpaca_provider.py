"""Alpaca Market Data v2 provider (real REST implementation).

Calls the Alpaca Market Data v2 REST API. Returns split/dividend-adjusted bars
honoring the strict :class:`~app.data.provider_base.MarketDataProvider`
contract. Credentials come from settings; missing credentials raise a clear
``RuntimeError`` — we never fabricate data.

API reference: https://docs.alpaca.markets/reference/stockbars
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
from app.data.provider_base import (
    BAR_COLUMNS,
    MarketDataProvider,
    empty_bars,
    normalize_bars,
)
from app.data.session_calendar import is_trading_day, now_et, session_bounds_utc
from app.enums import AssetType, MarketStatus
from app.logging_config import get_logger

logger = get_logger(__name__)

_TIMEFRAME_MAP = {
    "1Min": "1Min",
    "5Min": "5Min",
    "15Min": "15Min",
    "30Min": "30Min",
    "1H": "1Hour",
    "1Hour": "1Hour",
    "1Day": "1Day",
    "1D": "1Day",
}

_RETRYABLE = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
)


class _RetryableStatus(requests.exceptions.HTTPError):
    """Raised for 5xx / 429 responses so tenacity retries them."""


class AlpacaProvider(MarketDataProvider):
    """Market-data reads against Alpaca's v2 REST API."""

    name = "alpaca"

    def __init__(self) -> None:
        if not (settings.alpaca_api_key and settings.alpaca_secret_key):
            raise RuntimeError(
                "Alpaca credentials missing. Set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY in your .env to use the alpaca provider. "
                "This system never fabricates market data."
            )
        self._data_url = settings.alpaca_base_url.rstrip("/")
        # Trading API base (for assets / clock) is derived from the broker host.
        self._trading_url = "https://paper-api.alpaca.markets"
        self._feed = settings.alpaca_data_feed
        self._session = requests.Session()
        self._session.headers.update(
            {
                "APCA-API-KEY-ID": settings.alpaca_api_key,
                "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ HTTP
    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        reraise=True,
    )
    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = self._session.get(url, params=params, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _RetryableStatus(
                f"Alpaca {resp.status_code} for {url}: {resp.text[:200]}",
                response=resp,
            )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _parse_bars(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return empty_bars()
        df = pd.DataFrame(rows)
        # Alpaca bar fields: t (RFC3339), o,h,l,c,v.
        df = df.rename(
            columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
        )
        df["timestamp"] = pd.to_datetime(df["t"], utc=True)
        df = df.set_index("timestamp")[BAR_COLUMNS]
        return normalize_bars(df)

    # -------------------------------------------------------------- contract
    def get_historical_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {s: empty_bars() for s in symbols}
        if not symbols:
            return out
        tf = _TIMEFRAME_MAP.get(timeframe, timeframe)
        url = f"{self._data_url}/v2/stocks/bars"
        collected: dict[str, list[dict]] = {s: [] for s in symbols}
        page_token: str | None = None
        while True:
            params: dict[str, object] = {
                "symbols": ",".join(symbols),
                "timeframe": tf,
                "start": self._iso(start),
                "end": self._iso(end),
                "adjustment": "split",
                "feed": self._feed,
                "limit": 10000,
            }
            if page_token:
                params["page_token"] = page_token
            try:
                payload = self._get(url, params)
            except Exception as exc:  # one bad page should not crash the run
                logger.error("Alpaca historical_bars failed: %s", exc)
                break
            bars = payload.get("bars") or {}
            for sym, rows in bars.items():
                collected.setdefault(sym, []).extend(rows)
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        for sym in symbols:
            out[sym] = self._parse_bars(collected.get(sym, []))
        return out

    def get_latest_bars(
        self,
        symbols: list[str],
        lookback: int = 50,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        # Pull a generous window then trim to the last ``lookback`` completed bars.
        end = datetime.now(tz=UTC)
        # 15-min bars => ~26/day; widen window to cover weekends/holidays.
        days = max(5, (lookback // 13) + 5)
        start = end - pd.Timedelta(days=days)
        bars = self.get_historical_bars(symbols, start, end, timeframe)
        return {sym: df.tail(lookback) for sym, df in bars.items()}

    def get_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not symbols:
            return out
        url = f"{self._data_url}/v2/stocks/snapshots"
        try:
            payload = self._get(url, {"symbols": ",".join(symbols), "feed": self._feed})
        except Exception as exc:
            logger.error("Alpaca snapshots failed: %s", exc)
            return out
        for sym in symbols:
            snap = payload.get(sym) or {}
            trade = snap.get("latestTrade") or {}
            quote = snap.get("latestQuote") or {}
            daily = snap.get("dailyBar") or {}
            out[sym] = {
                "symbol": sym,
                "price": trade.get("p"),
                "bid": quote.get("bp"),
                "ask": quote.get("ap"),
                "volume": daily.get("v"),
                "timestamp": trade.get("t"),
            }
        return out

    def get_assets(self) -> list[dict]:
        url = f"{self._trading_url}/v2/assets"
        try:
            payload = self._get(url, {"status": "active", "asset_class": "us_equity"})
        except Exception as exc:
            logger.error("Alpaca get_assets failed: %s", exc)
            return []
        assets: list[dict] = []
        for a in payload if isinstance(payload, list) else []:
            if not a.get("tradable", False):
                continue
            asset_type = AssetType.ETF if a.get("etf") else AssetType.COMMON_STOCK
            assets.append(
                {
                    "symbol": a.get("symbol"),
                    "name": a.get("name"),
                    "exchange": a.get("exchange"),
                    "asset_type": asset_type.value,
                    "sector": None,  # Alpaca assets do not carry sector data.
                }
            )
        return assets

    def get_market_status(self) -> MarketStatus:
        url = f"{self._trading_url}/v2/clock"
        try:
            clock = self._get(url)
            if clock.get("is_open"):
                return MarketStatus.OPEN
        except Exception as exc:
            logger.warning("Alpaca clock failed (%s); inferring from calendar.", exc)
        # Fallback: infer from the session calendar in ET.
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
