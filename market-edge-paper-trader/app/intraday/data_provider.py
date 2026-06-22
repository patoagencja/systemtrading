"""Intraday data provider. Primary: yfinance. Optional: Alpaca if API key present."""
import os
import time
import datetime
import logging
from zoneinfo import ZoneInfo
from typing import Optional

import pandas as pd
import yfinance as yf

from app.intraday.session_manager import NY_TZ, SessionManager

log = logging.getLogger(__name__)

_ALPACA_KEY = os.getenv("ALPACA_API_KEY", "")
_ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", "")
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2.0

# yfinance interval map
_INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "1d": "1d",
}


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize columns to lowercase, ensure tz-aware index in ET."""
    if df is None or df.empty:
        return pd.DataFrame()
    # Flatten MultiIndex columns from yfinance
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() if c[0] else c[1].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(NY_TZ)
    df = df.sort_index()
    return df


def _filter_regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only bars within regular session 09:30-16:00 ET."""
    if df.empty:
        return df
    t = df.index.time
    mask = (t >= datetime.time(9, 30)) & (t < datetime.time(16, 0))
    return df[mask]


def _yf_download(ticker: str, start, end, interval: str) -> pd.DataFrame:
    """Download from yfinance with retries."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            df = yf.download(
                ticker, start=start, end=end,
                interval=interval, progress=False, auto_adjust=True,
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            log.warning(f"yfinance download {ticker} attempt {attempt+1}: {e}")
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_DELAY)
    return pd.DataFrame()


class IntradayDataProvider:
    """Fetch intraday and daily OHLCV data."""

    def __init__(self):
        self._use_alpaca = bool(_ALPACA_KEY and _ALPACA_SECRET)

    def get_intraday_bars(
        self,
        ticker: str,
        start,
        end,
        interval: str = "30m",
    ) -> pd.DataFrame:
        """Return OHLCV bars for period. Index is tz-aware (ET). Regular session only."""
        yf_interval = _INTERVAL_MAP.get(interval, interval)
        if isinstance(start, datetime.date) and not isinstance(start, datetime.datetime):
            start = start.isoformat()
        if isinstance(end, datetime.date) and not isinstance(end, datetime.datetime):
            end = end.isoformat()

        if self._use_alpaca:
            df = self._alpaca_bars(ticker, start, end, interval)
            if not df.empty:
                return df

        df = _yf_download(ticker, start, end, yf_interval)
        df = _normalize_df(df)
        df = _filter_regular_session(df)
        df = df.dropna(subset=["open", "high", "low", "close"])
        return df

    def get_daily_bars(self, ticker: str, start, end) -> pd.DataFrame:
        """Return daily OHLCV bars."""
        if isinstance(start, datetime.date) and not isinstance(start, datetime.datetime):
            start = start.isoformat()
        if isinstance(end, datetime.date) and not isinstance(end, datetime.datetime):
            end = end.isoformat()
        df = _yf_download(ticker, start, end, "1d")
        df = _normalize_df(df)
        df = df.dropna(subset=["open", "high", "low", "close"])
        return df

    def get_latest_complete_bar(
        self, ticker: str, interval: str = "30m"
    ) -> Optional[pd.Series]:
        """Return most recently COMPLETED bar. Never returns a partial/open bar."""
        now_et = SessionManager.get_current_et_time()
        # Fetch last 5 days to ensure we get at least one complete bar
        start = (now_et - datetime.timedelta(days=5)).date()
        end = (now_et + datetime.timedelta(days=1)).date()
        df = self.get_intraday_bars(ticker, start, end, interval)
        if df.empty:
            return None
        interval_minutes = _interval_to_minutes(interval)
        # Drop any bar that hasn't fully closed yet
        complete = df[df.index + pd.Timedelta(minutes=interval_minutes) <= now_et]
        if complete.empty:
            return None
        return complete.iloc[-1]

    def get_market_status(self) -> dict:
        """Return current market status dict."""
        now_et = SessionManager.get_current_et_time()
        status = SessionManager.get_market_status(now_et)
        return {
            "status": "OPEN" if status == "OPEN" else "CLOSED",
            "current_time_et": now_et.strftime("%H:%M:%S"),
            "session_date": now_et.date().isoformat(),
            "detail": status,
        }

    def check_data_coverage(self, ticker: str, interval: str = "30m") -> dict:
        """Return data coverage stats for a ticker."""
        try:
            end = datetime.date.today()
            start = end - datetime.timedelta(days=60)
            df = self.get_intraday_bars(ticker, start, end, interval)
            if df.empty:
                return {"error": "no data", "ticker": ticker}
            interval_minutes = _interval_to_minutes(interval)
            # Approximate expected bars: 6.5 hours * 2 bars/hour = 13 bars/day
            bars_per_day = int(390 / interval_minutes)
            trading_days = len({d.date() for d in df.index})
            expected = trading_days * bars_per_day
            return {
                "ticker": ticker,
                "first_date": df.index[0].isoformat(),
                "last_date": df.index[-1].isoformat(),
                "total_bars": len(df),
                "missing_bars": max(0, expected - len(df)),
                "sessions_count": trading_days,
            }
        except Exception as e:
            return {"error": str(e), "ticker": ticker}

    def _alpaca_bars(self, ticker: str, start, end, interval: str) -> pd.DataFrame:
        """Fetch bars from Alpaca if available."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            client = StockHistoricalDataClient(_ALPACA_KEY, _ALPACA_SECRET)
            tf_map = {"1m": TimeFrame.Minute, "5m": TimeFrame(5, TimeFrameUnit.Minute),
                      "15m": TimeFrame(15, TimeFrameUnit.Minute),
                      "30m": TimeFrame(30, TimeFrameUnit.Minute),
                      "1h": TimeFrame.Hour, "1d": TimeFrame.Day}
            tf = tf_map.get(interval, TimeFrame(30, TimeFrameUnit.Minute))
            req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=tf, start=start, end=end)
            bars = client.get_stock_bars(req)
            df = bars.df
            if df.empty:
                return pd.DataFrame()
            df = df.reset_index(level=0, drop=True) if isinstance(df.index, pd.MultiIndex) else df
            df = df.rename(columns={"open": "open", "high": "high", "low": "low",
                                     "close": "close", "volume": "volume"})
            df = _normalize_df(df)
            return _filter_regular_session(df)
        except Exception as e:
            log.debug(f"Alpaca fetch failed for {ticker}: {e}")
            return pd.DataFrame()


def _interval_to_minutes(interval: str) -> int:
    """Convert interval string to minutes."""
    mapping = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
    return mapping.get(interval, 30)
