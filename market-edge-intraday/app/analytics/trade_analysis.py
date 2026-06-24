"""Trade-level analysis: distributions, streaks, top trades and exit reasons.

All functions accept either ``list[ClosedTrade]`` or a trades ``DataFrame`` and
are defensive against empty / partial inputs.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from app.analytics.attribution import _pnl_column, _to_df
from app.logging_config import get_logger

logger = get_logger(__name__)


def _summary_stats(series: pd.Series) -> dict[str, float]:
    """Five-number-ish summary for a numeric series (NaN-safe)."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {
            "count": 0,
            "mean": 0.0,
            "std": 0.0,
            "min": 0.0,
            "p25": 0.0,
            "median": 0.0,
            "p75": 0.0,
            "max": 0.0,
        }
    return {
        "count": int(s.count()),
        "mean": float(s.mean()),
        "std": float(s.std(ddof=1)) if len(s) > 1 else 0.0,
        "min": float(s.min()),
        "p25": float(s.quantile(0.25)),
        "median": float(s.median()),
        "p75": float(s.quantile(0.75)),
        "max": float(s.max()),
    }


def trade_distribution(trades: Iterable[Any] | pd.DataFrame) -> dict[str, dict[str, float]]:
    """Summary statistics for r_multiple, return_pct and holding_bars.

    Returns a dict keyed by metric name, each value a summary-stats dict.
    Useful for rendering histograms / box plots downstream.
    """
    df = _to_df(trades)
    out: dict[str, dict[str, float]] = {}
    for key, col in (
        ("r_multiple", "r_multiple"),
        ("return_pct", "return_pct"),
        ("holding_bars", "holding_bars"),
    ):
        out[key] = _summary_stats(df[col]) if (not df.empty and col in df.columns) else (
            _summary_stats(pd.Series(dtype=float))
        )
    return out


def streaks(trades: Iterable[Any] | pd.DataFrame) -> dict[str, int]:
    """Longest win / loss streaks and current streak.

    Trades are ordered by exit_time (falling back to entry_time / input order).
    Win == net P&L > 0, loss == net P&L < 0; flat trades break both streaks.
    """
    df = _to_df(trades)
    empty = {"longest_win_streak": 0, "longest_loss_streak": 0, "current_streak": 0}
    if df.empty:
        return empty

    sort_col = next((c for c in ("exit_time", "entry_time") if c in df.columns), None)
    if sort_col is not None:
        df = df.sort_values(sort_col, kind="stable")

    pnl = pd.to_numeric(df[_pnl_column(df)], errors="coerce").fillna(0.0).to_numpy()
    longest_win = longest_loss = 0
    cur_win = cur_loss = 0
    for v in pnl:
        if v > 0:
            cur_win += 1
            cur_loss = 0
        elif v < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        longest_win = max(longest_win, cur_win)
        longest_loss = max(longest_loss, cur_loss)

    current = cur_win if cur_win else -cur_loss
    return {
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_streak": current,
    }


def top_trades(trades: Iterable[Any] | pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Return the ``n`` best and ``n`` worst trades by net P&L.

    The result has an added ``rank_kind`` column ('best' | 'worst') and is
    sorted best-first then worst-first.
    """
    df = _to_df(trades)
    if df.empty:
        return df
    pnl_col = _pnl_column(df)
    work = df.copy()
    work[pnl_col] = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0.0)
    best = work.nlargest(n, pnl_col).assign(rank_kind="best")
    worst = work.nsmallest(n, pnl_col).assign(rank_kind="worst")
    return pd.concat([best, worst], ignore_index=True)


def result_excluding_top(trades: Iterable[Any] | pd.DataFrame, n: int = 5) -> dict[str, float]:
    """Net P&L with and without the top-``n`` winning trades.

    Quantifies dependence on a handful of outliers. ``dependent`` is True when
    removing the top-``n`` flips the result non-positive.
    """
    df = _to_df(trades)
    if df.empty:
        return {"total": 0.0, "excluding_top": 0.0, "removed": 0.0, "dependent": False}
    pnl = pd.to_numeric(df[_pnl_column(df)], errors="coerce").fillna(0.0)
    total = float(pnl.sum())
    removed = float(pnl.sort_values(ascending=False).head(n).sum())
    excluding = total - removed
    return {
        "total": total,
        "excluding_top": excluding,
        "removed": removed,
        "dependent": bool(total > 0 and excluding <= 0),
    }


def mae_mfe_summary(trades: Iterable[Any] | pd.DataFrame) -> dict[str, Any]:
    """Maximum Adverse / Favourable Excursion summary, best-effort.

    The ``ClosedTrade`` dataclass and the ``trades`` table do not currently
    persist MAE/MFE. If columns named ``mae`` / ``mfe`` (or ``mae_r`` /
    ``mfe_r``) are present we summarise them; otherwise we return a clear
    ``available: False`` marker so callers can skip the section gracefully.
    """
    df = _to_df(trades)
    mae_col = next((c for c in ("mae", "mae_r", "max_adverse_excursion") if c in df.columns), None)
    mfe_col = next(
        (c for c in ("mfe", "mfe_r", "max_favorable_excursion") if c in df.columns), None
    )
    if df.empty or (mae_col is None and mfe_col is None):
        return {
            "available": False,
            "reason": "MAE/MFE not stored on ClosedTrade / trades table.",
        }
    out: dict[str, Any] = {"available": True}
    if mae_col is not None:
        out["mae"] = _summary_stats(df[mae_col])
    if mfe_col is not None:
        out["mfe"] = _summary_stats(df[mfe_col])
    return out


def exit_reason_breakdown(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Per exit-reason counts, share, net P&L and win rate."""
    cols = ["num_trades", "share", "net_pnl_pln", "win_rate"]
    df = _to_df(trades)
    if df.empty or "exit_reason" not in df.columns:
        return pd.DataFrame(columns=cols)

    pnl_col = _pnl_column(df)
    work = df.copy()
    work["_pnl"] = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0.0)
    total = len(work)

    records: list[dict[str, Any]] = []
    index: list[Any] = []
    for reason, grp in work.groupby("exit_reason", dropna=False):
        p = grp["_pnl"]
        num = int(len(p))
        records.append(
            {
                "num_trades": num,
                "share": num / total if total else 0.0,
                "net_pnl_pln": float(p.sum()),
                "win_rate": float((p > 0).sum()) / num if num else 0.0,
            }
        )
        index.append(reason)
    return pd.DataFrame.from_records(records, index=pd.Index(index, name="exit_reason"))[cols]


__all__ = [
    "trade_distribution",
    "streaks",
    "top_trades",
    "result_excluding_top",
    "mae_mfe_summary",
    "exit_reason_breakdown",
]
