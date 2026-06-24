"""TEST/DEMO ONLY — not real market data; never used by the live engine.

A deterministic synthetic market-data provider. Given a fixed RNG seed it
produces reproducible 15-minute OHLCV bars for a small symbol list (including
``"SPY"``) across regular US session hours (09:30-16:00 ET, 26 bars/day). It is
used by unit tests and as a clearly-labelled demo data source. It must NEVER be
selected for live paper trading.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from app.data.provider_base import MarketDataProvider, empty_bars, normalize_bars
from app.data.session_calendar import (
    expected_bar_starts,
    is_trading_day,
    now_et,
    previous_session,
    session_bounds_utc,
)
from app.enums import AssetType, MarketStatus
from app.logging_config import get_logger

logger = get_logger(__name__)

_SEED = 20240101

# Symbol -> (starting price, daily drift, annualised-ish vol knob, asset_type, sector).
_FIXTURE_SYMBOLS: dict[str, dict] = {
    "SPY": {"price": 450.0, "drift": 0.0002, "vol": 0.008, "type": AssetType.ETF, "sector": "BROAD_MARKET"},
    "AAPL": {"price": 190.0, "drift": 0.0003, "vol": 0.012, "type": AssetType.COMMON_STOCK, "sector": "TECHNOLOGY"},
    "MSFT": {"price": 410.0, "drift": 0.0003, "vol": 0.011, "type": AssetType.COMMON_STOCK, "sector": "TECHNOLOGY"},
    "JPM": {"price": 195.0, "drift": 0.0001, "vol": 0.013, "type": AssetType.COMMON_STOCK, "sector": "FINANCIALS"},
    "XOM": {"price": 110.0, "drift": 0.0001, "vol": 0.014, "type": AssetType.COMMON_STOCK, "sector": "ENERGY"},
    "JNJ": {"price": 155.0, "drift": 0.00005, "vol": 0.009, "type": AssetType.COMMON_STOCK, "sector": "HEALTHCARE"},
    "XLK": {"price": 200.0, "drift": 0.0003, "vol": 0.010, "type": AssetType.ETF, "sector": "TECHNOLOGY"},
    "NVDA": {"price": 120.0, "drift": 0.0005, "vol": 0.020, "type": AssetType.COMMON_STOCK, "sector": "TECHNOLOGY"},
}

_BASE_VOLUME = {
    "SPY": 80_000_000,
    "XLK": 8_000_000,
    "AAPL": 60_000_000,
    "MSFT": 30_000_000,
    "NVDA": 50_000_000,
    "JPM": 12_000_000,
    "XOM": 18_000_000,
    "JNJ": 9_000_000,
}


class FixtureProvider(MarketDataProvider):
    """Deterministic synthetic provider for tests and demos only."""

    name = "fixture"

    def __init__(self, seed: int = _SEED) -> None:
        self._seed = seed
        logger.info("FixtureProvider initialised (TEST/DEMO synthetic data, seed=%d).", seed)

    # ------------------------------------------------------------- synthesis
    def _symbol_seed(self, symbol: str) -> int:
        # Stable per-symbol seed so each symbol's series is reproducible.
        return self._seed + (abs(hash(symbol)) % 1_000_003)

    def _generate(self, symbol: str, starts: list[datetime]) -> pd.DataFrame:
        if not starts:
            return empty_bars()
        spec = _FIXTURE_SYMBOLS.get(
            symbol,
            {"price": 100.0, "drift": 0.0001, "vol": 0.012, "type": AssetType.COMMON_STOCK},
        )
        rng = np.random.default_rng(self._symbol_seed(symbol))
        n = len(starts)
        # 15-min log returns: small drift + gaussian shocks.
        per_bar_drift = spec["drift"] / 26.0
        per_bar_vol = spec["vol"] / np.sqrt(26.0)
        shocks = rng.normal(per_bar_drift, per_bar_vol, size=n)
        close = spec["price"] * np.exp(np.cumsum(shocks))
        prev_close = np.empty(n)
        prev_close[0] = spec["price"]
        prev_close[1:] = close[:-1]
        # Build OHLC around open=prev_close with intrabar wiggle.
        open_ = prev_close
        intrabar = np.abs(rng.normal(0, per_bar_vol, size=n)) * close
        high = np.maximum(open_, close) + intrabar
        low = np.minimum(open_, close) - intrabar
        low = np.maximum(low, 0.01)
        base_vol = _BASE_VOLUME.get(symbol, 2_000_000) / 26.0
        volume = (base_vol * (0.5 + rng.random(n))).round()
        volume = np.maximum(volume, 1.0)
        idx = pd.DatetimeIndex(
            [pd.Timestamp(s).tz_convert("UTC") for s in starts], name="timestamp"
        )
        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            },
            index=idx,
        )
        return normalize_bars(df)

    def _trading_days(self, start: datetime, end: datetime) -> list[date]:
        days = []
        cur = pd.Timestamp(start).tz_convert("UTC").date() if pd.Timestamp(start).tzinfo \
            else pd.Timestamp(start).date()
        end_d = pd.Timestamp(end).tz_convert("UTC").date() if pd.Timestamp(end).tzinfo \
            else pd.Timestamp(end).date()
        while cur <= end_d:
            if is_trading_day(cur):
                days.append(cur)
            cur = cur + timedelta(days=1)
        return days

    # -------------------------------------------------------------- contract
    def get_historical_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        days = self._trading_days(start, end)
        is_daily = timeframe in ("1Day", "1D")
        out: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            if is_daily:
                # One synthetic daily bar per session (aggregate of the day).
                day_starts = [session_bounds_utc(d)[0] for d in days if session_bounds_utc(d)]
                df = self._generate(sym, day_starts)
                # Inflate daily volume back to full-day scale.
                df["volume"] = df["volume"] * 26.0
                out[sym] = df
            else:
                starts: list[datetime] = []
                for d in days:
                    starts.extend(expected_bar_starts(d, timeframe))
                # Clip to the requested [start, end] window.
                lo = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo \
                    else pd.Timestamp(start, tz="UTC")
                hi = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo \
                    else pd.Timestamp(end, tz="UTC")
                starts = [s for s in starts if lo <= pd.Timestamp(s) <= hi]
                out[sym] = self._generate(sym, starts)
        return out

    def get_latest_bars(
        self,
        symbols: list[str],
        lookback: int = 50,
        timeframe: str = "15Min",
    ) -> dict[str, pd.DataFrame]:
        end = datetime.now(tz=UTC)
        days = max(5, (lookback // 13) + 5)
        start = end - timedelta(days=days)
        bars = self.get_historical_bars(symbols, start, end, timeframe)
        return {sym: df.tail(lookback) for sym, df in bars.items()}

    def get_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        # Use the last completed bar of the most recent session as "latest".
        ref_day = now_et().date()
        if not is_trading_day(ref_day):
            prev = previous_session(ref_day)
            ref_day = prev or ref_day
        bounds = session_bounds_utc(ref_day)
        out: dict[str, dict] = {}
        for sym in symbols:
            if bounds is None:
                out[sym] = {"symbol": sym, "price": None, "bid": None,
                            "ask": None, "volume": None, "timestamp": None}
                continue
            starts = expected_bar_starts(ref_day)
            df = self._generate(sym, starts)
            if df.empty:
                out[sym] = {"symbol": sym, "price": None, "bid": None,
                            "ask": None, "volume": None, "timestamp": None}
                continue
            last = df.iloc[-1]
            price = float(last["close"])
            spread = max(0.01, price * 0.0002)
            out[sym] = {
                "symbol": sym,
                "price": price,
                "bid": round(price - spread / 2, 4),
                "ask": round(price + spread / 2, 4),
                "volume": float(last["volume"]),
                "timestamp": df.index[-1].to_pydatetime(),
            }
        return out

    def get_assets(self) -> list[dict]:
        assets = []
        for sym, spec in _FIXTURE_SYMBOLS.items():
            assets.append(
                {
                    "symbol": sym,
                    "name": f"{sym} (synthetic)",
                    "exchange": "DEMO",
                    "asset_type": spec["type"].value,
                    "sector": spec.get("sector"),
                }
            )
        return assets

    def get_market_status(self) -> MarketStatus:
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
