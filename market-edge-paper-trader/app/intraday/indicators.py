"""Intraday and daily technical indicators."""
import datetime
import numpy as np
import pandas as pd


# ── helpers ──────────────────────────────────────────────────────────────────

def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's smoothed RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ATR."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return atr


# ── intraday indicators ───────────────────────────────────────────────────────

def compute_intraday_indicators(
    df: pd.DataFrame,
    session_date: datetime.date = None,
) -> pd.DataFrame:
    """
    Add intraday technical columns to df (30m bars).
    VWAP resets each calendar day.
    """
    if df.empty:
        return df

    df = df.copy()

    # Ensure we have required columns
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = np.nan

    # ── VWAP (resets per session day) ────────────────────────────────────────
    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["_tp"] = typical
    df["_date"] = df.index.date

    def _session_vwap(grp):
        cum_tp_vol = (grp["_tp"] * grp["volume"]).cumsum()
        cum_vol = grp["volume"].cumsum()
        result = cum_tp_vol / cum_vol.replace(0, np.nan)
        return result

    _vwap_result = df.groupby("_date", group_keys=False).apply(_session_vwap)
    # Handle both pandas versions: newer may return DataFrame
    if isinstance(_vwap_result, pd.DataFrame):
        _vwap_result = _vwap_result.iloc[:, 0]
    df["vwap"] = _vwap_result

    # VWAP z-score (session rolling std of close)
    def _session_zscore(grp):
        rolling_std = grp["close"].expanding().std()
        return (grp["close"] - grp["vwap"].reindex(grp.index)) / rolling_std.replace(0, np.nan)

    _zscore_result = df.groupby("_date", group_keys=False).apply(_session_zscore)
    if isinstance(_zscore_result, pd.DataFrame):
        _zscore_result = _zscore_result.iloc[:, 0]
    df["vwap_zscore"] = _zscore_result

    # ── EMAs ─────────────────────────────────────────────────────────────────
    df["ema9"]  = df["close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    # ── RSI and ATR ──────────────────────────────────────────────────────────
    df["rsi14"] = _wilder_rsi(df["close"], 14)
    df["atr14"] = _wilder_atr(df["high"], df["low"], df["close"], 14)

    # ── Rolling highs / lows ─────────────────────────────────────────────────
    df["rolling_high_10"] = df["high"].rolling(10).max()
    df["rolling_high_20"] = df["high"].rolling(20).max()
    df["rolling_low_10"]  = df["low"].rolling(10).min()

    # ── Volume ───────────────────────────────────────────────────────────────
    df["volume_sma20"]    = df["volume"].rolling(20).mean()
    df["relative_volume"] = df["volume"] / df["volume_sma20"].replace(0, np.nan)

    # ── Session return ────────────────────────────────────────────────────────
    def _session_open(grp):
        first_open = grp["open"].iloc[0]
        return (grp["close"] - first_open) / first_open if first_open != 0 else pd.Series(0.0, index=grp.index)

    _session_ret_result = df.groupby("_date", group_keys=False).apply(_session_open)
    if isinstance(_session_ret_result, pd.DataFrame):
        _session_ret_result = _session_ret_result.iloc[:, 0]
    df["session_return"] = _session_ret_result

    # ── Bar returns ───────────────────────────────────────────────────────────
    ret = df["close"].pct_change()
    df["return_2bars"] = (1 + ret) * (1 + ret.shift(1)) - 1
    df["return_4bars"] = df["close"].pct_change(4)

    # ── Distance metrics ─────────────────────────────────────────────────────
    df["distance_from_vwap"]  = (df["close"] - df["vwap"]) / df["vwap"].replace(0, np.nan)
    df["distance_from_ema20"] = (df["close"] - df["ema20"]) / df["ema20"].replace(0, np.nan)

    # ── Candle anatomy ───────────────────────────────────────────────────────
    df["bar_range"] = df["high"] - df["low"]
    _rng = df["bar_range"].replace(0, np.nan)
    df["body_pct"]        = (df["close"] - df["open"]).abs() / _rng
    df["upper_wick_pct"]  = (df["high"] - df[["open", "close"]].max(axis=1)) / _rng
    df["lower_wick_pct"]  = (df[["open", "close"]].min(axis=1) - df["low"]) / _rng

    # ── Intraday volatility ───────────────────────────────────────────────────
    df["intraday_volatility"] = ret.rolling(10).std()

    # Cleanup temp columns
    df.drop(columns=["_tp", "_date"], inplace=True, errors="ignore")

    return df


def compute_daily_indicators_for_filter(df: pd.DataFrame) -> pd.DataFrame:
    """Add daily technical columns used for trend filters."""
    if df.empty:
        return df

    df = df.copy()

    df["sma20"]  = df["close"].rolling(20).mean()
    df["sma50"]  = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    df["atr14"]  = _wilder_atr(df["high"], df["low"], df["close"], 14)
    df["rsi14"]  = _wilder_rsi(df["close"], 14)

    df["return_20d"] = df["close"].pct_change(20)
    df["return_60d"] = df["close"].pct_change(60)

    dollar_vol = df["close"] * df["volume"]
    df["avg_dollar_volume_20d"] = dollar_vol.rolling(20).mean()
    df["volume_sma20"]          = df["volume"].rolling(20).mean()

    daily_ret = df["close"].pct_change()
    df["daily_volatility"] = daily_ret.rolling(20).std() * np.sqrt(252)

    return df
