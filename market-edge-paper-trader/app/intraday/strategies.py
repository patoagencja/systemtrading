"""Four intraday trading strategies. Each returns IntradaySignal or None."""
import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from app.intraday.config import MIN_REWARD_RISK, INTRADAY_MIN_HISTORY_BARS
from app.intraday.session_manager import NY_TZ

log = logging.getLogger(__name__)


@dataclass
class IntradaySignal:
    ticker: str
    strategy: str
    score: float
    signal_timestamp: datetime.datetime
    signal_bar_close: float
    planned_entry: float
    stop_price: float
    target_price: float
    reward_risk: float
    sector: str = ""
    market_regime: str = "NEUTRAL"
    entry_reason: str = ""


def _last(df: pd.DataFrame, col: str, n: int = 0):
    """Safe last-n value from column."""
    if col not in df.columns or len(df) <= n:
        return np.nan
    return df[col].iloc[-(1 + n)]


def _has_enough_bars(df: pd.DataFrame, n: int = None) -> bool:
    if n is None:
        n = INTRADAY_MIN_HISTORY_BARS
    return df is not None and not df.empty and len(df) >= n


def _validate_signal(
    ticker: str,
    strategy: str,
    signal_ts: datetime.datetime,
    bar_close: float,
    entry: float,
    stop: float,
    target: float,
    sector: str,
    market_regime: str,
    entry_reason: str,
) -> Optional[IntradaySignal]:
    """Build signal and validate R:R."""
    if stop >= entry or target <= entry:
        return None
    rr = (target - entry) / (entry - stop)
    if rr < MIN_REWARD_RISK:
        return None
    return IntradaySignal(
        ticker=ticker,
        strategy=strategy,
        score=0.0,  # filled by scoring module
        signal_timestamp=signal_ts,
        signal_bar_close=bar_close,
        planned_entry=entry,
        stop_price=stop,
        target_price=target,
        reward_risk=round(rr, 3),
        sector=sector,
        market_regime=market_regime,
        entry_reason=entry_reason,
    )


def _get_et_time(df: pd.DataFrame) -> datetime.time:
    """Get ET time of last bar."""
    ts = df.index[-1]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=NY_TZ)
    else:
        ts = ts.astimezone(NY_TZ)
    return ts.time()


# ── Strategy 1: VWAP Mean Reversion ──────────────────────────────────────────

def strategy_vwap_mean_reversion(
    ticker: str,
    intraday_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    spy_intraday_df: pd.DataFrame = None,
    session_date: datetime.date = None,
) -> Optional[IntradaySignal]:
    """INTRADAY_VWAP_MEAN_REVERSION: Buy oversold dips to VWAP."""
    STRATEGY = "INTRADAY_VWAP_MEAN_REVERSION"

    if not _has_enough_bars(intraday_df, INTRADAY_MIN_HISTORY_BARS):
        return None
    if daily_df is None or daily_df.empty or len(daily_df) < 200:
        return None

    # ── Daily conditions ─────────────────────────────────────────────────────
    d = daily_df.iloc[-1]
    sma50  = d.get("sma50", np.nan)
    sma200 = d.get("sma200", np.nan)
    close_d = d.get("close", np.nan)
    avg_dv = d.get("avg_dollar_volume_20d", np.nan)

    if any(pd.isna(v) for v in [sma50, sma200, close_d]):
        return None
    if close_d < sma200:
        return None
    if sma50 < sma200:
        return None
    if not pd.isna(avg_dv) and avg_dv < 50_000_000:
        return None

    # ── Intraday conditions ──────────────────────────────────────────────────
    t = _get_et_time(intraday_df)
    if not (datetime.time(10, 0) <= t <= datetime.time(14, 0)):
        return None

    c0   = _last(intraday_df, "close")
    o0   = _last(intraday_df, "open")
    vwap = _last(intraday_df, "vwap")
    zscore = _last(intraday_df, "vwap_zscore")
    rsi  = _last(intraday_df, "rsi14")
    atr  = _last(intraday_df, "atr14")
    ret2 = _last(intraday_df, "return_2bars")
    l0   = _last(intraday_df, "low")
    l1   = _last(intraday_df, "low", 1)
    vol  = _last(intraday_df, "volume")
    low_wick = _last(intraday_df, "lower_wick_pct")
    up_wick  = _last(intraday_df, "upper_wick_pct")
    prev_low = l1 if not pd.isna(l1) else l0

    if any(pd.isna(v) for v in [c0, o0, vwap, zscore, rsi, atr, ret2, vol]):
        return None

    if c0 >= vwap:
        return None
    if zscore > -1.5:
        return None
    if rsi > 35:
        return None
    if ret2 >= 0:
        return None
    # Reversal candle: bullish body, lower wick dominates, closed above prev low
    if c0 <= o0:
        return None
    if not pd.isna(low_wick) and not pd.isna(up_wick) and low_wick <= up_wick:
        return None
    if c0 <= prev_low:
        return None
    if vol <= 0:
        return None

    # ── Trade levels ─────────────────────────────────────────────────────────
    entry  = c0  # planned: next bar open ≈ signal bar close
    stop1  = min(l0, l1) if not pd.isna(l1) else l0
    stop2  = entry - atr
    stop   = min(stop1, stop2)
    target = min(vwap, entry + 1.5 * (entry - stop))

    signal_ts = intraday_df.index[-1]

    return _validate_signal(
        ticker, STRATEGY, signal_ts, c0, entry, stop, target,
        sector="", market_regime="NEUTRAL",
        entry_reason=f"zscore={zscore:.2f},rsi={rsi:.1f}",
    )


# ── Strategy 2: Opening Range Breakout ───────────────────────────────────────

def strategy_opening_range_breakout(
    ticker: str,
    intraday_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    spy_intraday_df: pd.DataFrame = None,
    session_date: datetime.date = None,
) -> Optional[IntradaySignal]:
    """INTRADAY_OPENING_RANGE_BREAKOUT: Buy breakout above first 30m range high."""
    STRATEGY = "INTRADAY_OPENING_RANGE_BREAKOUT"

    if not _has_enough_bars(intraday_df, 4):
        return None
    if daily_df is None or daily_df.empty or len(daily_df) < 50:
        return None

    # ── Daily conditions ─────────────────────────────────────────────────────
    d = daily_df.iloc[-1]
    sma50  = d.get("sma50", np.nan)
    close_d = d.get("close", np.nan)
    atr14_d = d.get("atr14", np.nan)

    if any(pd.isna(v) for v in [sma50, close_d, atr14_d]):
        return None
    if close_d < sma50:
        return None

    # ── Intraday: time gate ───────────────────────────────────────────────────
    t = _get_et_time(intraday_df)
    if t < datetime.time(10, 0):
        return None  # ORB bar not yet complete

    # ── Opening range = first session bar ────────────────────────────────────
    if session_date is not None:
        session_bars = intraday_df[intraday_df.index.date == session_date]
    else:
        today = intraday_df.index[-1].date()
        session_bars = intraday_df[intraday_df.index.date == today]

    if len(session_bars) < 2:
        return None

    orb_bar = session_bars.iloc[0]
    orb_high = orb_bar["high"]
    orb_low  = orb_bar["low"]
    orb_range = orb_high - orb_low

    # ORB range must not be unusually wide
    if not pd.isna(atr14_d) and orb_range > 3 * atr14_d:
        return None

    c0   = _last(intraday_df, "close")
    vwap = _last(intraday_df, "vwap")
    rsi  = _last(intraday_df, "rsi14")
    atr  = _last(intraday_df, "atr14")
    rvol = _last(intraday_df, "relative_volume")

    if any(pd.isna(v) for v in [c0, vwap, rsi, atr, rvol]):
        return None

    if c0 <= orb_high:
        return None
    if c0 < vwap:
        return None
    if rvol < 1.5:
        return None
    if not (50 <= rsi <= 75):
        return None

    # ── Trade levels ─────────────────────────────────────────────────────────
    entry  = c0
    stop1  = entry - atr
    stop2  = orb_high - 0.01  # just below ORB high
    stop   = min(stop1, stop2)
    target = entry + 1.5 * (entry - stop)

    signal_ts = intraday_df.index[-1]

    return _validate_signal(
        ticker, STRATEGY, signal_ts, c0, entry, stop, target,
        sector="", market_regime="NEUTRAL",
        entry_reason=f"orb_high={orb_high:.2f},rvol={rvol:.2f}",
    )


# ── Strategy 3: Momentum Continuation ────────────────────────────────────────

def strategy_momentum_continuation(
    ticker: str,
    intraday_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    spy_intraday_df: pd.DataFrame = None,
    session_date: datetime.date = None,
) -> Optional[IntradaySignal]:
    """INTRADAY_MOMENTUM_CONTINUATION: Buy new intraday highs with trend."""
    STRATEGY = "INTRADAY_MOMENTUM_CONTINUATION"

    if not _has_enough_bars(intraday_df, INTRADAY_MIN_HISTORY_BARS):
        return None
    if daily_df is None or daily_df.empty or len(daily_df) < 200:
        return None

    # ── Daily conditions ─────────────────────────────────────────────────────
    d = daily_df.iloc[-1]
    sma50  = d.get("sma50", np.nan)
    sma200 = d.get("sma200", np.nan)
    close_d = d.get("close", np.nan)

    if any(pd.isna(v) for v in [sma50, sma200, close_d]):
        return None
    if close_d < sma50 or close_d < sma200:
        return None
    if sma50 < sma200:
        return None

    # ── Time gate ─────────────────────────────────────────────────────────────
    t = _get_et_time(intraday_df)
    if t >= datetime.time(14, 0):
        return None

    c0   = _last(intraday_df, "close")
    vwap = _last(intraday_df, "vwap")
    ema9 = _last(intraday_df, "ema9")
    ema20= _last(intraday_df, "ema20")
    rh20 = _last(intraday_df, "rolling_high_20")
    rsi  = _last(intraday_df, "rsi14")
    atr  = _last(intraday_df, "atr14")
    rvol = _last(intraday_df, "relative_volume")

    if any(pd.isna(v) for v in [c0, vwap, ema9, ema20, rh20, rsi, atr, rvol]):
        return None

    if c0 < vwap:
        return None
    if ema9 <= ema20:
        return None
    if c0 <= rh20:
        return None
    if rvol < 1.3:
        return None
    if not (55 <= rsi <= 78):
        return None

    # ── Trade levels ─────────────────────────────────────────────────────────
    entry  = c0
    stop1  = entry - atr
    stop2  = ema20
    stop   = max(stop1, stop2) if stop2 < entry else stop1  # more conservative
    stop   = min(stop, entry - 0.001)  # ensure stop < entry
    target = entry + 2.0 * (entry - stop)

    signal_ts = intraday_df.index[-1]

    return _validate_signal(
        ticker, STRATEGY, signal_ts, c0, entry, stop, target,
        sector="", market_regime="NEUTRAL",
        entry_reason=f"rh20={rh20:.2f},rvol={rvol:.2f},rsi={rsi:.1f}",
    )


# ── Strategy 4: Relative Strength Pullback ────────────────────────────────────

def strategy_relative_strength_pullback(
    ticker: str,
    intraday_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    spy_intraday_df: pd.DataFrame = None,
    session_date: datetime.date = None,
) -> Optional[IntradaySignal]:
    """INTRADAY_RELATIVE_STRENGTH_PULLBACK: Buy RS stocks on EMA20 pullback."""
    STRATEGY = "INTRADAY_RELATIVE_STRENGTH_PULLBACK"

    if not _has_enough_bars(intraday_df, INTRADAY_MIN_HISTORY_BARS):
        return None
    if daily_df is None or daily_df.empty or len(daily_df) < 200:
        return None

    # ── Daily conditions ─────────────────────────────────────────────────────
    d = daily_df.iloc[-1]
    sma50   = d.get("sma50", np.nan)
    sma200  = d.get("sma200", np.nan)
    close_d = d.get("close", np.nan)
    ret20   = d.get("return_20d", np.nan)

    if any(pd.isna(v) for v in [sma50, sma200, close_d]):
        return None
    if close_d < sma50 or close_d < sma200:
        return None

    # Compare 20d return vs SPY
    if spy_intraday_df is not None and not spy_intraday_df.empty and "return_20d" in spy_intraday_df.columns:
        pass  # would compare daily returns
    # Fallback: just require positive 20d return
    if not pd.isna(ret20) and ret20 <= 0:
        return None

    # ── Intraday conditions ───────────────────────────────────────────────────
    t = _get_et_time(intraday_df)
    if not (datetime.time(10, 0) <= t <= datetime.time(14, 30)):
        return None

    c0   = _last(intraday_df, "close")
    o0   = _last(intraday_df, "open")
    vwap = _last(intraday_df, "vwap")
    ema20= _last(intraday_df, "ema20")
    rsi  = _last(intraday_df, "rsi14")
    atr  = _last(intraday_df, "atr14")
    dist_ema20 = _last(intraday_df, "distance_from_ema20")
    vol  = _last(intraday_df, "volume")
    vol1 = _last(intraday_df, "volume", 1)
    sr   = _last(intraday_df, "session_return")

    if any(pd.isna(v) for v in [c0, o0, vwap, ema20, rsi, atr, dist_ema20]):
        return None

    # Stock session return > 0 (relative strength)
    if not pd.isna(sr) and sr <= 0:
        return None

    # Pullback to EMA20: distance within -2% to +1%
    if not (-0.02 <= dist_ema20 <= 0.01):
        return None
    if not (40 <= rsi <= 60):
        return None
    # Recovery candle: bullish
    if c0 <= o0:
        return None
    # Volume increasing
    if not pd.isna(vol) and not pd.isna(vol1) and vol1 > 0 and vol < vol1:
        return None

    # ── Trade levels ─────────────────────────────────────────────────────────
    entry  = c0
    stop1  = entry - atr
    stop2  = ema20 * 0.99  # 1% below EMA20
    stop   = max(stop1, stop2) if stop2 < entry else stop1
    stop   = min(stop, entry - 0.001)

    # Target: 1.5R or attempt previous session high
    target = entry + 1.5 * (entry - stop)

    signal_ts = intraday_df.index[-1]

    return _validate_signal(
        ticker, STRATEGY, signal_ts, c0, entry, stop, target,
        sector="", market_regime="NEUTRAL",
        entry_reason=f"dist_ema20={dist_ema20:.3f},rsi={rsi:.1f}",
    )


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all_intraday_strategies(
    ticker: str,
    intraday_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    spy_intraday_df: pd.DataFrame = None,
    sector: str = None,
    session_date: datetime.date = None,
) -> list[IntradaySignal]:
    """Run all 4 strategies; return list of valid signals."""
    funcs = [
        strategy_vwap_mean_reversion,
        strategy_opening_range_breakout,
        strategy_momentum_continuation,
        strategy_relative_strength_pullback,
    ]
    signals = []
    for fn in funcs:
        try:
            sig = fn(ticker, intraday_df, daily_df, spy_intraday_df, session_date)
            if sig is not None:
                if sector:
                    sig.sector = sector
                signals.append(sig)
        except Exception as e:
            log.debug(f"Strategy {fn.__name__} error for {ticker}: {e}")
    return signals
