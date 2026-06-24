"""Benchmarks to compare the strategies against.

All benchmarks use the same dates, the same starting capital and the same
currency assumption (constant fallback USD/PLN in backtest) so the comparison is
apples-to-apples.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings

ET = ZoneInfo("America/New_York")


@dataclass
class BenchmarkResult:
    name: str
    total_return: float
    final_equity_pln: float
    equity_curve: pd.DataFrame


def _daily_closes(bars: pd.DataFrame) -> pd.Series:
    """Session-close series (one value per ET date) from intraday bars."""
    if bars is None or bars.empty:
        return pd.Series(dtype=float)
    idx = bars.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    et_dates = idx.tz_convert(ET).date
    df = bars.copy()
    df["_d"] = et_dates
    return df.groupby("_d")["close"].last()


def buy_and_hold(bars: pd.DataFrame, name: str, initial_capital_pln: float) -> BenchmarkResult:
    """Buy at the first session close, hold to the last."""
    closes = _daily_closes(bars)
    if closes.empty:
        return BenchmarkResult(name, 0.0, initial_capital_pln, pd.DataFrame())
    norm = closes / closes.iloc[0]
    equity = norm * initial_capital_pln
    curve = pd.DataFrame({"session_date": closes.index, "equity_pln": equity.values})
    return BenchmarkResult(name, float(norm.iloc[-1] - 1.0), float(equity.iloc[-1]), curve)


def intraday_open_to_close(bars: pd.DataFrame, name: str, initial_capital_pln: float) -> BenchmarkResult:
    """Buy each day's open, sell each day's close — compounded, no overnight."""
    if bars is None or bars.empty:
        return BenchmarkResult(name, 0.0, initial_capital_pln, pd.DataFrame())
    idx = bars.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df = bars.copy()
    df["_d"] = idx.tz_convert(ET).date
    daily = df.groupby("_d").agg(open=("open", "first"), close=("close", "last"))
    daily_ret = (daily["close"] / daily["open"] - 1.0).fillna(0.0)
    equity = (1.0 + daily_ret).cumprod() * initial_capital_pln
    curve = pd.DataFrame({"session_date": daily.index, "equity_pln": equity.values})
    total = float(equity.iloc[-1] / initial_capital_pln - 1.0) if not equity.empty else 0.0
    return BenchmarkResult(name, total, float(equity.iloc[-1]) if not equity.empty else initial_capital_pln, curve)


def cash(initial_capital_pln: float, days: int) -> BenchmarkResult:
    return BenchmarkResult("CASH_0PCT", 0.0, initial_capital_pln, pd.DataFrame())


def compute_benchmarks(
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    start: date,
    end: date,
    initial_capital_pln: float | None = None,
) -> list[BenchmarkResult]:
    cap = initial_capital_pln or settings.initial_capital_pln
    out: list[BenchmarkResult] = []
    spy = bars_by_symbol.get("SPY")
    qqq = bars_by_symbol.get("QQQ")
    if spy is not None:
        out.append(buy_and_hold(spy, "SPY_BUY_HOLD", cap))
        out.append(intraday_open_to_close(spy, "SPY_INTRADAY_O2C", cap))
    if qqq is not None:
        out.append(buy_and_hold(qqq, "QQQ_BUY_HOLD", cap))
    out.append(cash(cap, 0))

    # Equal-weighted universe buy & hold (excluding benchmark ETFs).
    members = [s for s in bars_by_symbol if s not in ("SPY", "QQQ")]
    if members:
        rets = []
        for s in members:
            closes = _daily_closes(bars_by_symbol[s])
            if len(closes) > 1:
                rets.append(closes.iloc[-1] / closes.iloc[0] - 1.0)
        if rets:
            avg = sum(rets) / len(rets)
            out.append(BenchmarkResult("UNIVERSE_EQUAL_WEIGHT", float(avg),
                                       cap * (1 + avg), pd.DataFrame()))
    return out
