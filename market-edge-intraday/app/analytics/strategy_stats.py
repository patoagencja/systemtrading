"""Per-strategy summary statistics and the research-verdict classifier.

``strategy_summary`` produces a compact per-strategy table for dashboards and
reports. ``strategy_status`` implements the project's promotion ladder
(FAIL / INCONCLUSIVE / PROMISING / POTENTIAL_EDGE) from the specification.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.attribution import _pnl_column, _profit_factor, _to_df
from app.enums import StrategyStatus
from app.logging_config import get_logger

logger = get_logger(__name__)

_SUMMARY_COLUMNS = [
    "trades",
    "win_rate",
    "profit_factor",
    "expectancy_r",
    "net_pnl_pln",
    "avg_holding_bars",
    "max_drawdown",
]

# Promotion-ladder thresholds (from the spec).
_MIN_TRADES_INCONCLUSIVE = 100
_MIN_TRADES_EDGE = 500
_PF_PROMISING = 1.1
_PF_EDGE = 1.2
_MAX_DD_EDGE = 0.15
_VERY_HIGH_DD = 0.40


def _max_drawdown_from_pnl(pnl: pd.Series) -> float:
    """Max drawdown (positive fraction) of a cumulative P&L curve.

    Drawdown is expressed relative to the running peak equity, assuming the
    curve starts from a notional peak so an all-loss series still yields a
    sensible value. Returns 0.0 when there is no drawdown / no data.
    """
    if pnl.empty:
        return 0.0
    equity = pnl.cumsum()
    running_max = equity.cummax()
    # Guard the denominator: use peak magnitude, floor at 1 to avoid blow-ups.
    denom = running_max.abs().clip(lower=1.0)
    dd = (running_max - equity) / denom
    val = float(dd.max())
    if not np.isfinite(val) or val < 0:
        return 0.0
    return val


def strategy_summary(trades: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Return a per-strategy summary DataFrame.

    Columns: ``trades``, ``win_rate``, ``profit_factor``, ``expectancy_r``,
    ``net_pnl_pln``, ``avg_holding_bars``, ``max_drawdown``. Indexed by
    strategy name. Empty input yields an empty (correctly shaped) frame.
    """
    df = _to_df(trades)
    if df.empty or "strategy" not in df.columns:
        return pd.DataFrame(columns=_SUMMARY_COLUMNS)

    pnl_col = _pnl_column(df)
    work = df.copy()
    work["_pnl"] = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0.0)
    work["_r"] = pd.to_numeric(work.get("r_multiple", np.nan), errors="coerce")
    work["_hold"] = pd.to_numeric(work.get("holding_bars", np.nan), errors="coerce")

    records: list[dict[str, Any]] = []
    index: list[Any] = []
    for strat, grp in work.groupby("strategy", dropna=False):
        p = grp["_pnl"]
        num = int(len(p))
        records.append(
            {
                "trades": num,
                "win_rate": float((p > 0).sum()) / num if num else 0.0,
                "profit_factor": _profit_factor(p),
                "expectancy_r": float(grp["_r"].mean()) if grp["_r"].notna().any() else 0.0,
                "net_pnl_pln": float(p.sum()),
                "avg_holding_bars": (
                    float(grp["_hold"].mean()) if grp["_hold"].notna().any() else 0.0
                ),
                "max_drawdown": _max_drawdown_from_pnl(p.reset_index(drop=True)),
            }
        )
        index.append(strat)

    out = pd.DataFrame.from_records(records, index=pd.Index(index, name="strategy"))
    return out[_SUMMARY_COLUMNS]


def strategy_status(
    trades: Iterable[Any] | pd.DataFrame,
    oos_positive: bool,
    walk_forward_positive_share: float,
    max_dd: float,
    stress_positive: bool,
) -> StrategyStatus:
    """Classify a strategy onto the research promotion ladder.

    Parameters
    ----------
    trades:
        Closed trades for the strategy (BASE cost scenario, in/sample+OOS).
    oos_positive:
        Whether out-of-sample net P&L is positive.
    walk_forward_positive_share:
        Fraction (0..1) of walk-forward windows that were net positive.
    max_dd:
        Maximum drawdown as a positive fraction (e.g. 0.12 == 12%).
    stress_positive:
        Whether the strategy is still net positive under the STRESS cost
        scenario (edge survives costs).

    Rules (from spec)
    -----------------
    FAIL          : PF < 1, or negative net P&L, or edge gone after costs
                    (not stress_positive), or very high drawdown.
    INCONCLUSIVE  : too few trades (< 100) or otherwise unstable.
    PROMISING     : PF > 1.1, positive OOS, reasonable drawdown.
    POTENTIAL_EDGE: >= 500 trades, PF > 1.2, positive OOS, majority of
                    walk-forward windows positive, max DD < 15%, result not
                    dependent on the top-5 trades, and still positive in STRESS.
    """
    df = _to_df(trades)
    num = int(len(df))
    if df.empty:
        return StrategyStatus.INCONCLUSIVE

    pnl = pd.to_numeric(df[_pnl_column(df)], errors="coerce").fillna(0.0)
    net = float(pnl.sum())
    pf = _profit_factor(pnl)
    dd = float(max_dd) if max_dd is not None else 0.0

    # Is the net result dependent on the top-5 trades? (i.e. without them it
    # flips non-positive => fragile / dependent.)
    top5 = pnl.sort_values(ascending=False).head(5)
    net_without_top5 = net - float(top5.sum())
    not_dependent_on_top5 = net_without_top5 > 0

    # --- FAIL: any disqualifier --------------------------------------------
    if pf < 1.0 or net <= 0 or not stress_positive or dd >= _VERY_HIGH_DD:
        return StrategyStatus.FAIL

    # --- INCONCLUSIVE: not enough evidence ---------------------------------
    if num < _MIN_TRADES_INCONCLUSIVE:
        return StrategyStatus.INCONCLUSIVE

    # --- POTENTIAL_EDGE: the full bar ---------------------------------------
    if (
        num >= _MIN_TRADES_EDGE
        and pf > _PF_EDGE
        and oos_positive
        and walk_forward_positive_share > 0.5
        and dd < _MAX_DD_EDGE
        and not_dependent_on_top5
        and stress_positive
    ):
        return StrategyStatus.POTENTIAL_EDGE

    # --- PROMISING ----------------------------------------------------------
    if pf > _PF_PROMISING and oos_positive and dd < _VERY_HIGH_DD:
        return StrategyStatus.PROMISING

    return StrategyStatus.INCONCLUSIVE


__all__ = ["strategy_summary", "strategy_status"]
