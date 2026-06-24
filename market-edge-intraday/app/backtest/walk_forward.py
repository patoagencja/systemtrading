"""Walk-forward and out-of-sample evaluation.

Two analyses:
  * a single development / validation / out-of-sample split (60 / 20 / 20);
  * a rolling walk-forward (6 months develop, 3 months test, step 1 month).

Parameters are FIXED before looking at the out-of-sample data — this module
does NOT optimise on the OOS slice. It only measures stability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class WalkForwardResult:
    splits: dict = field(default_factory=dict)
    windows: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    oos_positive: bool = False
    windows_table: pd.DataFrame | None = None


def _run_slice(base: BacktestConfig, bars, start: date, end: date) -> BacktestResult | None:
    cfg = BacktestConfig(
        symbols=base.symbols, start=start, end=end, strategies=base.strategies,
        cost_scenario=base.cost_scenario, initial_capital_pln=base.initial_capital_pln,
        min_score=base.min_score, benchmark_symbol=base.benchmark_symbol,
    )
    try:
        return run_backtest(cfg, bars_by_symbol=bars)
    except RuntimeError as exc:
        logger.warning("Walk-forward slice %s..%s skipped: %s", start, end, exc)
        return None


def _metrics_row(label: str, res: BacktestResult | None) -> dict:
    if res is None:
        return {"window": label, "num_trades": 0, "net_pnl": 0.0, "profit_factor": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "positive": False}
    m = res.metrics
    return {
        "window": label,
        "num_trades": m.get("num_trades", 0),
        "net_pnl": round(m.get("net_pnl", 0.0), 2),
        "profit_factor": round(m.get("profit_factor", 0.0), 3),
        "sharpe": round(m.get("sharpe", 0.0), 3),
        "max_drawdown": round(m.get("max_drawdown", 0.0), 4),
        "positive": m.get("net_pnl", 0.0) > 0,
    }


def run_walk_forward(
    config: BacktestConfig,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    *,
    develop_months: int = 6,
    test_months: int = 3,
    step_months: int = 1,
) -> WalkForwardResult:
    bars = bars_by_symbol
    if bars is None:
        from app.backtest.engine import _load_bars

        bars = _load_bars(config)

    total_days = (config.end - config.start).days
    if total_days < 30:
        return WalkForwardResult(summary={"status": "INCONCLUSIVE", "reason": "range too short"})

    # 60 / 20 / 20 split.
    dev_end = config.start + timedelta(days=int(total_days * 0.6))
    val_end = config.start + timedelta(days=int(total_days * 0.8))
    dev = _run_slice(config, bars, config.start, dev_end)
    val = _run_slice(config, bars, dev_end + timedelta(days=1), val_end)
    oos = _run_slice(config, bars, val_end + timedelta(days=1), config.end)

    splits = {
        "development": _metrics_row("development", dev),
        "validation": _metrics_row("validation", val),
        "out_of_sample": _metrics_row("out_of_sample", oos),
    }
    oos_positive = splits["out_of_sample"]["net_pnl"] > 0

    # Rolling windows.
    windows: list[dict] = []
    win_start = config.start
    n = 0
    while True:
        d_end = win_start + timedelta(days=develop_months * 30)
        t_end = d_end + timedelta(days=test_months * 30)
        if t_end > config.end:
            break
        test = _run_slice(config, bars, d_end + timedelta(days=1), t_end)
        windows.append(_metrics_row(f"window_{n}:{d_end}..{t_end}", test))
        win_start = win_start + timedelta(days=step_months * 30)
        n += 1

    wins = [w for w in windows if w["num_trades"] > 0]
    n_win = max(1, len(wins))
    summary = {
        "n_windows": len(wins),
        "share_positive": sum(w["positive"] for w in wins) / n_win,
        "share_pf_gt_1": sum(w["profit_factor"] > 1.0 for w in wins) / n_win,
        "share_pf_gt_1_2": sum(w["profit_factor"] > 1.2 for w in wins) / n_win,
        "share_positive_sharpe": sum(w["sharpe"] > 0 for w in wins) / n_win,
        "share_dd_over_limit": sum(abs(w["max_drawdown"]) > 0.15 for w in wins) / n_win,
        "oos_positive": oos_positive,
    }
    return WalkForwardResult(
        splits=splits, windows=windows, summary=summary, oos_positive=oos_positive,
        windows_table=pd.DataFrame(windows),
    )
