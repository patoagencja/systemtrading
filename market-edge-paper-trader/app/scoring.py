import numpy as np
import pandas as pd


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def score_mean_reversion(row: pd.Series) -> float:
    score = 0.0

    # Trend component (25 pts)
    dist200 = row.get("distance_from_sma200_pct", 0)
    if not pd.isna(dist200):
        if dist200 > 10:
            score += 25
        elif dist200 > 5:
            score += 18
        elif dist200 > 0:
            score += 10

    # RSI component (25 pts) – lower is better for mean reversion
    rsi = row.get("rsi14", 50)
    if not pd.isna(rsi):
        if rsi < 25:
            score += 25
        elif rsi < 30:
            score += 20
        elif rsi < 35:
            score += 12

    # Volume component (20 pts)
    vol_ratio = row.get("volume_ratio", 1.0)
    if not pd.isna(vol_ratio):
        if vol_ratio >= 1.5:
            score += 20
        elif vol_ratio >= 1.2:
            score += 15
        elif vol_ratio >= 0.8:
            score += 8

    # 5-day return (15 pts) – deeper pullback is better
    ret5 = row.get("return_5d", 0)
    if not pd.isna(ret5):
        if ret5 <= -0.08:
            score += 15
        elif ret5 <= -0.05:
            score += 12
        elif ret5 <= -0.03:
            score += 8

    # Volatility (15 pts) – moderate volatility
    vol20 = row.get("volatility_20d", 0.3)
    if not pd.isna(vol20):
        if 0.20 <= vol20 <= 0.50:
            score += 15
        elif 0.15 <= vol20 <= 0.60:
            score += 8

    return _clamp(score)


def score_momentum_breakout(row: pd.Series) -> float:
    score = 0.0

    # Trend (20 pts)
    dist50 = row.get("distance_from_sma50_pct", 0)
    dist200 = row.get("distance_from_sma200_pct", 0)
    if not pd.isna(dist50) and not pd.isna(dist200):
        if dist200 > 15 and dist50 > 5:
            score += 20
        elif dist200 > 5:
            score += 12

    # RSI (25 pts) – momentum zone
    rsi = row.get("rsi14", 50)
    if not pd.isna(rsi):
        if 60 <= rsi <= 70:
            score += 25
        elif 55 <= rsi <= 75:
            score += 18
        elif 50 <= rsi <= 75:
            score += 10

    # Volume (30 pts) – strong volume on breakout
    vol_ratio = row.get("volume_ratio", 1.0)
    if not pd.isna(vol_ratio):
        if vol_ratio >= 2.0:
            score += 30
        elif vol_ratio >= 1.5:
            score += 22
        elif vol_ratio >= 1.3:
            score += 14

    # Volatility (15 pts)
    vol20 = row.get("volatility_20d", 0.3)
    if not pd.isna(vol20):
        if 0.15 <= vol20 <= 0.45:
            score += 15
        elif vol20 < 0.60:
            score += 7

    # Daily return (10 pts)
    daily_ret = row.get("daily_return", 0)
    if not pd.isna(daily_ret):
        if daily_ret >= 0.02:
            score += 10
        elif daily_ret >= 0.01:
            score += 6

    return _clamp(score)


def score_pullback_trend(row: pd.Series) -> float:
    score = 0.0

    # Trend (25 pts)
    dist200 = row.get("distance_from_sma200_pct", 0)
    if not pd.isna(dist200):
        if dist200 > 15:
            score += 25
        elif dist200 > 8:
            score += 18
        elif dist200 > 3:
            score += 10

    # RSI (25 pts) – neutral zone
    rsi = row.get("rsi14", 50)
    if not pd.isna(rsi):
        if 48 <= rsi <= 55:
            score += 25
        elif 45 <= rsi <= 58:
            score += 18
        elif 40 <= rsi <= 60:
            score += 10

    # Distance from SMA50 (25 pts)
    dist50 = row.get("distance_from_sma50_pct", 0)
    if not pd.isna(dist50):
        if -1.0 <= dist50 <= 2.0:
            score += 25
        elif -2.0 <= dist50 <= 3.5:
            score += 15
        elif -3.0 <= dist50 <= 5.0:
            score += 8

    # Volume (15 pts)
    vol_ratio = row.get("volume_ratio", 1.0)
    if not pd.isna(vol_ratio):
        if vol_ratio >= 1.2:
            score += 15
        elif vol_ratio >= 0.9:
            score += 8

    # Volatility (10 pts)
    vol20 = row.get("volatility_20d", 0.3)
    if not pd.isna(vol20):
        if 0.15 <= vol20 <= 0.40:
            score += 10
        elif vol20 < 0.55:
            score += 5

    return _clamp(score)


def score_etf_relative_strength(
    row: pd.Series,
    stock_ret_20d: float,
    spy_ret_20d: float,
) -> float:
    score = 0.0

    # RS strength (35 pts)
    rs_diff = stock_ret_20d - spy_ret_20d
    if rs_diff >= 0.10:
        score += 35
    elif rs_diff >= 0.05:
        score += 25
    elif rs_diff >= 0.02:
        score += 15

    # Trend (25 pts)
    dist200 = row.get("distance_from_sma200_pct", 0)
    if not pd.isna(dist200):
        if dist200 > 15:
            score += 25
        elif dist200 > 8:
            score += 18
        elif dist200 > 3:
            score += 10

    # Volume (20 pts)
    vol_ratio = row.get("volume_ratio", 1.0)
    if not pd.isna(vol_ratio):
        if vol_ratio >= 1.5:
            score += 20
        elif vol_ratio >= 1.2:
            score += 12
        elif vol_ratio >= 1.1:
            score += 7

    # RSI (20 pts)
    rsi = row.get("rsi14", 50)
    if not pd.isna(rsi):
        if 50 <= rsi <= 70:
            score += 20
        elif 45 <= rsi <= 75:
            score += 10

    return _clamp(score)
