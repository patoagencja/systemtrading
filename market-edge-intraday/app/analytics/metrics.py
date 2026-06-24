"""Performance metrics computed from closed trades and an equity curve.

This is the single source of truth for performance numbers — used by the
backtester, the reports and the dashboard. ``compute_metrics`` accepts a list of
:class:`ClosedTrade` (or a trades DataFrame) plus an equity curve and returns a
flat dict of metric_name -> value.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

from app.domain import ClosedTrade
from app.enums import StrategyStatus

TRADING_DAYS = 252


def trades_to_df(trades: Iterable[ClosedTrade] | pd.DataFrame) -> pd.DataFrame:
    if isinstance(trades, pd.DataFrame):
        return trades.copy()
    rows = []
    for t in trades:
        rows.append(
            {
                "symbol": t.symbol,
                "strategy": str(t.strategy),
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "net_pnl_pln": t.net_pnl_pln,
                "net_pnl_usd": t.net_pnl_usd,
                "gross_pnl_usd": t.gross_pnl_usd,
                "return_pct": t.return_pct,
                "r_multiple": t.r_multiple,
                "holding_bars": t.holding_bars,
                "exit_reason": str(t.exit_reason),
                "costs_usd": t.costs_usd,
                "slippage_usd": t.slippage_usd,
                "fx_pnl_pln": t.fx_pnl_pln,
            }
        )
    return pd.DataFrame(rows)


def _profit_factor(pnl: pd.Series) -> float:
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max
    return float(dd.min())


def _streaks(pnl: Sequence[float]) -> tuple[int, int]:
    longest_win = longest_loss = cur_win = cur_loss = 0
    for p in pnl:
        if p > 0:
            cur_win += 1
            cur_loss = 0
        elif p < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        longest_win = max(longest_win, cur_win)
        longest_loss = max(longest_loss, cur_loss)
    return longest_win, longest_loss


def compute_metrics(
    trades: Iterable[ClosedTrade] | pd.DataFrame,
    equity_curve: pd.DataFrame | None = None,
    *,
    initial_capital: float = 1_000_000.0,
    run_mode: str = "BACKTEST",
) -> dict:
    """Compute the full metrics dict. Safe on empty input (returns zeros)."""
    df = trades_to_df(trades)
    n = len(df)
    metrics: dict[str, float | int | str] = {"run_mode": run_mode, "num_trades": n}

    if n == 0:
        metrics.update({k: 0.0 for k in (
            "total_return", "cagr", "sharpe", "sortino", "calmar", "max_drawdown",
            "profit_factor", "win_rate", "expectancy", "expectancy_r", "avg_win",
            "avg_loss", "payoff_ratio", "median_trade", "trades_per_day",
            "trades_per_month", "avg_holding_bars", "time_in_market", "turnover",
            "costs", "slippage", "gross_pnl", "net_pnl", "longest_win_streak",
            "longest_loss_streak", "result_without_top5", "result_without_top10",
            "annualized_volatility",
        )})
        return metrics

    pnl = df["net_pnl_pln"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    metrics["net_pnl"] = float(pnl.sum())
    metrics["gross_pnl"] = float(df.get("gross_pnl_usd", pd.Series([0])).sum())
    metrics["costs"] = float(df.get("costs_usd", pd.Series([0])).sum())
    metrics["slippage"] = float(df.get("slippage_usd", pd.Series([0])).sum())
    metrics["win_rate"] = float((pnl > 0).mean())
    metrics["profit_factor"] = _profit_factor(pnl)
    metrics["avg_win"] = float(wins.mean()) if not wins.empty else 0.0
    metrics["avg_loss"] = float(losses.mean()) if not losses.empty else 0.0
    metrics["payoff_ratio"] = (
        abs(metrics["avg_win"] / metrics["avg_loss"]) if metrics["avg_loss"] else 0.0
    )
    metrics["expectancy"] = float(pnl.mean())
    metrics["median_trade"] = float(pnl.median())
    if "r_multiple" in df:
        metrics["expectancy_r"] = float(df["r_multiple"].astype(float).mean())
    if "holding_bars" in df:
        metrics["avg_holding_bars"] = float(df["holding_bars"].astype(float).mean())

    # Top-trade dependence.
    ranked = pnl.sort_values(ascending=False)
    metrics["result_without_top5"] = float(ranked.iloc[5:].sum()) if n > 5 else 0.0
    metrics["result_without_top10"] = float(ranked.iloc[10:].sum()) if n > 10 else 0.0

    lw, ll = _streaks(pnl.tolist())
    metrics["longest_win_streak"] = lw
    metrics["longest_loss_streak"] = ll

    # Time span.
    if "entry_time" in df and df["entry_time"].notna().any():
        days = pd.to_datetime(df["entry_time"]).dt.normalize().nunique()
        span_days = max(1, days)
        metrics["trades_per_day"] = n / span_days
        metrics["trades_per_month"] = n / max(1, span_days / 21)
    metrics["turnover"] = float(df.get("gross_pnl_usd", pd.Series([0])).abs().sum())

    # Equity-curve metrics.
    if equity_curve is not None and not equity_curve.empty:
        eq = equity_curve.copy()
        if "timestamp" in eq:
            eq = eq.set_index("timestamp")
        equity = eq["equity_pln"].astype(float)
        metrics["total_return"] = float(equity.iloc[-1] / initial_capital - 1.0)
        metrics["max_drawdown"] = _max_drawdown(equity)
        # Daily returns for ratios.
        daily = equity.resample("1D").last().dropna() if isinstance(
            equity.index, pd.DatetimeIndex
        ) else equity
        rets = daily.pct_change().dropna()
        n_days = max(1, len(rets))
        if not rets.empty and rets.std() > 0:
            metrics["annualized_volatility"] = float(rets.std() * np.sqrt(TRADING_DAYS))
            metrics["sharpe"] = float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS))
            downside = rets[rets < 0]
            metrics["sortino"] = float(
                rets.mean() / downside.std() * np.sqrt(TRADING_DAYS)
            ) if not downside.empty and downside.std() > 0 else 0.0
        else:
            metrics["annualized_volatility"] = 0.0
            metrics["sharpe"] = 0.0
            metrics["sortino"] = 0.0
        years = n_days / TRADING_DAYS
        if years > 0 and equity.iloc[-1] > 0:
            metrics["cagr"] = float((equity.iloc[-1] / initial_capital) ** (1 / years) - 1.0)
        else:
            metrics["cagr"] = 0.0
        mdd = abs(metrics["max_drawdown"])
        metrics["calmar"] = float(metrics["cagr"] / mdd) if mdd > 0 else 0.0
        metrics["time_in_market"] = float((equity.diff().abs() > 0).mean())
    else:
        metrics["total_return"] = float(pnl.sum() / initial_capital)
        for k in ("cagr", "sharpe", "sortino", "calmar", "max_drawdown",
                  "annualized_volatility", "time_in_market"):
            metrics[k] = 0.0

    return metrics


def classify_strategy(
    *,
    num_trades: int,
    profit_factor_base: float,
    net_pnl: float,
    oos_positive: bool,
    walk_forward_positive_share: float,
    max_drawdown: float,
    depends_on_top5: bool,
    stress_positive: bool,
) -> StrategyStatus:
    """Map backtest evidence to a research verdict (see spec section 29)."""
    mdd = abs(max_drawdown)
    if profit_factor_base < 1.0 or net_pnl <= 0 or not stress_positive or mdd > 0.30:
        return StrategyStatus.FAIL
    if num_trades < 100:
        return StrategyStatus.INCONCLUSIVE
    if (
        num_trades >= 500
        and profit_factor_base > 1.2
        and oos_positive
        and walk_forward_positive_share > 0.5
        and mdd < 0.15
        and not depends_on_top5
        and stress_positive
    ):
        return StrategyStatus.POTENTIAL_EDGE
    if profit_factor_base > 1.1 and oos_positive and mdd < 0.25:
        return StrategyStatus.PROMISING
    return StrategyStatus.INCONCLUSIVE
