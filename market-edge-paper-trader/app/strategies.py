import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
from app.config import STRATEGY_MAX_HOLDING, MAX_TP_PCT


@dataclass
class Signal:
    ticker: str
    strategy: str
    score: float
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    rsi: float
    volume_ratio: float
    reason: str
    max_holding_days: int


def _stop_and_tp(entry: float, atr: float, risk_ratio: float = 2.0, reward_ratio: float = 2.0):
    stop = entry - risk_ratio * atr
    risk = entry - stop
    tp = entry + reward_ratio * risk
    # Hard cap: TP no further than MAX_TP_PCT above entry
    tp_cap = entry * (1 + MAX_TP_PCT)
    tp = min(tp, tp_cap)
    return stop, tp


def strategy_mean_reversion_uptrend(df: pd.DataFrame, ticker: str) -> Optional[Signal]:
    if len(df) < 220:
        return None
    row = df.iloc[-1]
    close = row["close"]
    sma200 = row["sma200"]
    sma50 = row["sma50"]
    rsi = row["rsi14"]
    atr = row["atr14"]
    vol_ratio = row["volume_ratio"]
    return_5d = row.get("return_5d", np.nan)

    if pd.isna(sma200) or pd.isna(sma50) or pd.isna(rsi) or pd.isna(atr) or atr <= 0:
        return None

    conditions = [
        close > sma200,
        sma50 > sma200,
        rsi < 35,
        not pd.isna(return_5d) and return_5d <= -0.03,
        close >= sma200,
        not pd.isna(vol_ratio) and vol_ratio >= 0.8,
    ]
    if not all(conditions):
        return None

    stop, tp = _stop_and_tp(close, atr, risk_ratio=2.0, reward_ratio=1.5)
    if stop >= close:
        return None

    from app.scoring import score_mean_reversion
    score = score_mean_reversion(row)
    if score < 0:
        return None

    reason = (f"Above SMA200, SMA50>SMA200, RSI={rsi:.1f}<35, "
              f"5d_return={return_5d*100:.1f}%, vol_ratio={vol_ratio:.2f}")
    return Signal(
        ticker=ticker,
        strategy="MEAN_REVERSION_UPTREND",
        score=score,
        entry_price=close,
        stop_loss=stop,
        take_profit=tp,
        atr=atr,
        rsi=rsi,
        volume_ratio=vol_ratio,
        reason=reason,
        max_holding_days=STRATEGY_MAX_HOLDING["MEAN_REVERSION_UPTREND"],
    )


def strategy_momentum_breakout(df: pd.DataFrame, ticker: str) -> Optional[Signal]:
    if len(df) < 220:
        return None
    row = df.iloc[-1]
    close = row["close"]
    sma50 = row["sma50"]
    sma200 = row["sma200"]
    rsi = row["rsi14"]
    atr = row["atr14"]
    vol_ratio = row["volume_ratio"]
    high_20d = row["high_20d"]

    if pd.isna(sma50) or pd.isna(sma200) or pd.isna(rsi) or pd.isna(atr) or atr <= 0 or pd.isna(high_20d):
        return None

    conditions = [
        close > sma50,
        close > sma200,
        sma50 > sma200,
        close >= high_20d * 0.998,
        not pd.isna(vol_ratio) and vol_ratio >= 1.3,
        50 <= rsi <= 75,
    ]
    if not all(conditions):
        return None

    stop, tp = _stop_and_tp(close, atr, risk_ratio=2.0, reward_ratio=2.0)
    if stop >= close:
        return None

    from app.scoring import score_momentum_breakout
    score = score_momentum_breakout(row)
    if score < 0:
        return None

    reason = (f"Breakout above 20d high={high_20d:.2f}, RSI={rsi:.1f}, "
              f"vol_ratio={vol_ratio:.2f}, SMA50>SMA200")
    return Signal(
        ticker=ticker,
        strategy="MOMENTUM_BREAKOUT",
        score=score,
        entry_price=close,
        stop_loss=stop,
        take_profit=tp,
        atr=atr,
        rsi=rsi,
        volume_ratio=vol_ratio,
        reason=reason,
        max_holding_days=STRATEGY_MAX_HOLDING["MOMENTUM_BREAKOUT"],
    )


def strategy_pullback_trend(df: pd.DataFrame, ticker: str) -> Optional[Signal]:
    if len(df) < 220:
        return None
    row = df.iloc[-1]
    close = row["close"]
    open_ = row["open"]
    sma50 = row["sma50"]
    sma200 = row["sma200"]
    rsi = row["rsi14"]
    atr = row["atr14"]
    low_20d = row["low_20d"]
    dist_sma50 = row["distance_from_sma50_pct"]

    if pd.isna(sma50) or pd.isna(sma200) or pd.isna(rsi) or pd.isna(atr) or atr <= 0:
        return None

    conditions = [
        close > sma200,
        sma50 > sma200,
        not pd.isna(dist_sma50) and -3.0 <= dist_sma50 <= 5.0,
        40 <= rsi <= 60,
        close > open_,
    ]
    if not all(conditions):
        return None

    atr_stop = close - 2 * atr
    stop = min(low_20d if not pd.isna(low_20d) else atr_stop, atr_stop)
    if stop >= close or stop <= 0:
        return None
    risk = close - stop
    tp = close + 2 * risk

    from app.scoring import score_pullback_trend
    score = score_pullback_trend(row)
    if score < 0:
        return None

    reason = (f"Pullback to SMA50 ({dist_sma50:.1f}%), RSI={rsi:.1f}, "
              f"green candle, SMA200 uptrend")
    return Signal(
        ticker=ticker,
        strategy="PULLBACK_TREND",
        score=score,
        entry_price=close,
        stop_loss=stop,
        take_profit=tp,
        atr=atr,
        rsi=rsi,
        volume_ratio=float(row.get("volume_ratio", 1.0)),
        reason=reason,
        max_holding_days=STRATEGY_MAX_HOLDING["PULLBACK_TREND"],
    )


def strategy_etf_relative_strength(
    df: pd.DataFrame,
    ticker: str,
    spy_df: pd.DataFrame,
    sector_etf_df: Optional[pd.DataFrame] = None,
) -> Optional[Signal]:
    if len(df) < 220:
        return None

    row = df.iloc[-1]
    close = row["close"]
    sma50 = row["sma50"]
    sma200 = row["sma200"]
    atr = row["atr14"]
    vol_ratio = row["volume_ratio"]

    if pd.isna(sma50) or pd.isna(sma200) or pd.isna(atr) or atr <= 0:
        return None

    if not (close > sma50 and close > sma200):
        return None

    if sector_etf_df is not None and not sector_etf_df.empty:
        etf_row = sector_etf_df.iloc[-1]
        etf_sma50 = etf_row.get("sma50")
        etf_close = etf_row.get("close")
        if pd.isna(etf_sma50) or pd.isna(etf_close) or etf_close <= etf_sma50:
            return None

    if spy_df is None or spy_df.empty or len(spy_df) < 25:
        return None

    try:
        stock_ret = (df["close"].iloc[-1] / df["close"].iloc[-21] - 1) if len(df) >= 21 else None
        spy_ret = (spy_df["close"].iloc[-1] / spy_df["close"].iloc[-21] - 1) if len(spy_df) >= 21 else None
    except Exception:
        return None

    if stock_ret is None or spy_ret is None or stock_ret <= spy_ret:
        return None

    if pd.isna(vol_ratio) or vol_ratio < 1.1:
        return None

    stop, tp = _stop_and_tp(close, atr, risk_ratio=2.0, reward_ratio=2.0)
    if stop >= close:
        return None

    from app.scoring import score_etf_relative_strength
    score = score_etf_relative_strength(row, stock_ret, spy_ret)
    if score < 0:
        return None

    rsi = row.get("rsi14", 50)
    reason = (f"RS vs SPY: stock={stock_ret*100:.1f}% vs SPY={spy_ret*100:.1f}%, "
              f"vol_ratio={vol_ratio:.2f}, above SMA50/SMA200")
    return Signal(
        ticker=ticker,
        strategy="ETF_RELATIVE_STRENGTH",
        score=score,
        entry_price=close,
        stop_loss=stop,
        take_profit=tp,
        atr=atr,
        rsi=float(rsi) if not pd.isna(rsi) else 50.0,
        volume_ratio=float(vol_ratio),
        reason=reason,
        max_holding_days=STRATEGY_MAX_HOLDING["ETF_RELATIVE_STRENGTH"],
    )


def run_all_strategies(
    ticker: str,
    df: pd.DataFrame,
    spy_df: pd.DataFrame,
    sector_etf_df: Optional[pd.DataFrame] = None,
) -> list[Signal]:
    signals = []
    for fn in [
        lambda: strategy_mean_reversion_uptrend(df, ticker),
        lambda: strategy_momentum_breakout(df, ticker),
        lambda: strategy_pullback_trend(df, ticker),
        lambda: strategy_etf_relative_strength(df, ticker, spy_df, sector_etf_df),
    ]:
        try:
            sig = fn()
            if sig is not None:
                signals.append(sig)
        except Exception as e:
            print(f"  [warn] strategy error for {ticker}: {e}")
    return signals
