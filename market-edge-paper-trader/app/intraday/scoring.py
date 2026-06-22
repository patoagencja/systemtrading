"""Score intraday signals 0-100."""
import numpy as np
import pandas as pd

from app.intraday.strategies import IntradaySignal


def score_signal(
    signal: IntradaySignal,
    intraday_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    market_regime: str = "NEUTRAL",
) -> float:
    """Score a signal 0-100 based on quality factors."""
    total = 0.0

    def _last(df, col, n=0):
        if df is None or df.empty or col not in df.columns or len(df) <= n:
            return np.nan
        return df[col].iloc[-(1 + n)]

    # ── 1. Daily trend quality (0-20) ────────────────────────────────────────
    if daily_df is not None and not daily_df.empty:
        d = daily_df.iloc[-1]
        close_d = d.get("close", np.nan)
        sma50   = d.get("sma50", np.nan)
        sma200  = d.get("sma200", np.nan)
        if not any(pd.isna(v) for v in [close_d, sma50, sma200]):
            if close_d > sma200 and sma50 > sma200:
                total += 20
            elif close_d > sma200:
                total += 12
            elif close_d > sma50:
                total += 6

    # ── 2. Liquidity (0-10) ───────────────────────────────────────────────────
    avg_dv = np.nan
    if daily_df is not None and not daily_df.empty:
        avg_dv = daily_df.iloc[-1].get("avg_dollar_volume_20d", np.nan)
    if not pd.isna(avg_dv):
        if avg_dv > 200_000_000:
            total += 10
        elif avg_dv > 100_000_000:
            total += 8
        elif avg_dv > 50_000_000:
            total += 6

    # ── 3. Relative strength vs SPY (0-15) ────────────────────────────────────
    sr = _last(intraday_df, "session_return")
    # Without SPY comparison, award partial points based on positive session return
    if not pd.isna(sr):
        if sr > 0.01:
            total += 15
        elif sr > 0:
            total += 10
        else:
            total += 5

    # ── 4. Intraday volume (0-15) ─────────────────────────────────────────────
    rvol = _last(intraday_df, "relative_volume")
    if not pd.isna(rvol):
        if rvol > 2.0:
            total += 15
        elif rvol > 1.5:
            total += 12
        elif rvol > 1.2:
            total += 8
        elif rvol > 1.0:
            total += 5

    # ── 5. VWAP position (0-10) ───────────────────────────────────────────────
    dist_vwap = _last(intraday_df, "distance_from_vwap")
    strat = signal.strategy
    if not pd.isna(dist_vwap):
        if "MEAN_REVERSION" in strat:
            # Further below VWAP = more extreme dip
            if dist_vwap < -0.03:
                total += 10
            elif dist_vwap < -0.02:
                total += 8
            elif dist_vwap < -0.01:
                total += 5
        else:
            # Momentum: above VWAP is good
            if dist_vwap > 0.01:
                total += 10
            elif dist_vwap >= 0:
                total += 5

    # ── 6. Signal strength (0-15) ─────────────────────────────────────────────
    rsi = _last(intraday_df, "rsi14")
    if "MEAN_REVERSION" in strat:
        # RSI oversold = stronger signal
        if not pd.isna(rsi):
            if rsi < 25:
                total += 15
            elif rsi < 30:
                total += 12
            elif rsi < 35:
                total += 8
    elif "BREAKOUT" in strat or "MOMENTUM" in strat:
        # RSI in bullish zone
        if not pd.isna(rsi):
            if 60 <= rsi <= 72:
                total += 15
            elif 55 <= rsi < 60 or 72 < rsi <= 78:
                total += 8
    elif "PULLBACK" in strat:
        # RSI at 50 (neutral reversion)
        if not pd.isna(rsi):
            if 45 <= rsi <= 55:
                total += 15
            elif 40 <= rsi < 45 or 55 < rsi <= 60:
                total += 8

    # ── 7. Reward/risk ratio (0-10) ───────────────────────────────────────────
    rr = signal.reward_risk
    if rr > 2.0:
        total += 10
    elif rr > 1.8:
        total += 8
    elif rr > 1.5:
        total += 6
    elif rr > 1.2:
        total += 4

    # ── 8. Market regime (0-5) ────────────────────────────────────────────────
    regime_pts = {"RISK_ON": 5, "NEUTRAL": 3, "RISK_OFF": 0, "HIGH_VOLATILITY": 1}
    total += regime_pts.get(market_regime, 3)

    return round(min(total, 100.0), 1)
