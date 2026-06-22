"""Compute comprehensive intraday backtest performance metrics."""
import math
from collections import defaultdict
from typing import Optional

import numpy as np
import pandas as pd


def _safe_div(a, b, default=0.0):
    return a / b if b and b != 0 else default


def compute_intraday_metrics(
    trades: list[dict],
    snapshots: list[dict],
    initial_capital: float,
) -> dict:
    """Return comprehensive metrics dict from trade + snapshot lists."""

    if not trades:
        return {"error": "no trades", "total_trades": 0}

    # ── Basic P&L ─────────────────────────────────────────────────────────────
    net_pnls = [t.get("net_pnl_pln", 0) or 0 for t in trades]
    gross_pnls = [t.get("gross_pnl_pln", 0) or 0 for t in trades]
    costs    = [abs((t.get("commission_pln") or 0) + (t.get("spread_cost_pln") or 0)
                    + (t.get("slippage_cost_pln") or 0)) for t in trades]

    total_net = sum(net_pnls)
    total_gross = sum(gross_pnls)
    total_costs = sum(costs)

    wins  = [p for p in net_pnls if p > 0]
    losses= [p for p in net_pnls if p <= 0]

    win_rate = _safe_div(len(wins), len(trades)) * 100
    profit_factor = _safe_div(sum(wins), abs(sum(losses))) if losses else float("inf")
    avg_win  = _safe_div(sum(wins), len(wins)) if wins else 0.0
    avg_loss = _safe_div(sum(losses), len(losses)) if losses else 0.0
    avg_win_loss = _safe_div(abs(avg_win), abs(avg_loss)) if avg_loss else float("inf")
    expectancy_pln = _safe_div(total_net, len(trades))

    # Expectancy in R
    r_multiples = [t.get("r_multiple", 0) or 0 for t in trades]
    expectancy_r = _safe_div(sum(r_multiples), len(r_multiples))

    # ── Holding time ──────────────────────────────────────────────────────────
    hold_mins = [t.get("holding_minutes", 0) or 0 for t in trades]
    avg_hold  = float(np.mean(hold_mins)) if hold_mins else 0.0
    med_hold  = float(np.median(hold_mins)) if hold_mins else 0.0

    # ── Daily stats from snapshots ────────────────────────────────────────────
    sessions_count = len(snapshots)
    trades_per_day = _safe_div(len(trades), sessions_count) if sessions_count else 0.0
    daily_pnls     = [s.get("daily_pnl_pln", 0) or 0 for s in snapshots]
    profitable_sessions = sum(1 for p in daily_pnls if p > 0)
    pct_profitable_sessions = _safe_div(profitable_sessions, sessions_count) * 100

    best_session  = max(daily_pnls) if daily_pnls else 0.0
    worst_session = min(daily_pnls) if daily_pnls else 0.0

    # ── Drawdown from snapshots ────────────────────────────────────────────────
    equities = [s.get("equity_pln", initial_capital) for s in snapshots]
    max_dd_pct = 0.0
    peak = initial_capital
    for eq in equities:
        peak = max(peak, eq)
        dd = _safe_div(peak - eq, peak) if peak > 0 else 0.0
        max_dd_pct = max(max_dd_pct, dd)

    # Max daily loss
    max_daily_loss = min(daily_pnls) if daily_pnls else 0.0

    # ── Consecutive losses ────────────────────────────────────────────────────
    max_consec = 0
    cur_consec = 0
    daily_loss_limit_hits = 0
    daily_loss_limit_pct  = 0.01  # from config
    for i, s in enumerate(snapshots):
        dp = s.get("daily_pnl_pln", 0) or 0
        eq = s.get("equity_pln", initial_capital)
        if dp < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0
        if dp < -eq * daily_loss_limit_pct:
            daily_loss_limit_hits += 1

    # Max consecutive trade losses
    max_consec_trades = 0
    cur_consec_t = 0
    for p in net_pnls:
        if p <= 0:
            cur_consec_t += 1
            max_consec_trades = max(max_consec_trades, cur_consec_t)
        else:
            cur_consec_t = 0

    # ── Total return ──────────────────────────────────────────────────────────
    final_equity = equities[-1] if equities else initial_capital + total_net
    total_return_pct = _safe_div(final_equity - initial_capital, initial_capital) * 100

    # ── Results by strategy ───────────────────────────────────────────────────
    by_strategy: dict[str, list] = defaultdict(list)
    for t in trades:
        by_strategy[t.get("strategy", "UNKNOWN")].append(t.get("net_pnl_pln", 0) or 0)

    results_by_strategy = {}
    for strat, pnls in by_strategy.items():
        w = [p for p in pnls if p > 0]
        l = [p for p in pnls if p <= 0]
        results_by_strategy[strat] = {
            "trades": len(pnls),
            "win_rate_pct": round(_safe_div(len(w), len(pnls)) * 100, 1),
            "total_pnl_pln": round(sum(pnls), 2),
            "avg_pnl_pln": round(_safe_div(sum(pnls), len(pnls)), 2),
            "profit_factor": round(_safe_div(sum(w), abs(sum(l))) if l else float("inf"), 3),
        }

    # ── Results by entry hour ─────────────────────────────────────────────────
    by_hour: dict[int, list] = defaultdict(list)
    for t in trades:
        ts = t.get("entry_ts") or t.get("entry_timestamp", "")
        try:
            if isinstance(ts, str) and ts:
                hr = int(ts[11:13])
            elif hasattr(ts, "hour"):
                hr = ts.hour
            else:
                hr = -1
        except Exception:
            hr = -1
        if hr >= 0:
            by_hour[hr].append(t.get("net_pnl_pln", 0) or 0)

    results_by_hour = {
        hr: {
            "trades": len(pnls),
            "total_pnl_pln": round(sum(pnls), 2),
            "win_rate_pct": round(_safe_div(sum(1 for p in pnls if p > 0), len(pnls)) * 100, 1),
        }
        for hr, pnls in sorted(by_hour.items())
    }

    # ── Results by day of week ────────────────────────────────────────────────
    dow_names = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
    by_dow: dict[str, list] = defaultdict(list)
    for t in trades:
        sd = t.get("session_date")
        try:
            if isinstance(sd, str) and sd:
                d = pd.Timestamp(sd)
                dow = dow_names[d.dayofweek] if d.dayofweek < 5 else "Weekend"
            else:
                dow = "Unknown"
        except Exception:
            dow = "Unknown"
        by_dow[dow].append(t.get("net_pnl_pln", 0) or 0)

    results_by_dow = {
        dow: {
            "trades": len(pnls),
            "total_pnl_pln": round(sum(pnls), 2),
            "win_rate_pct": round(_safe_div(sum(1 for p in pnls if p > 0), len(pnls)) * 100, 1),
        }
        for dow, pnls in by_dow.items()
    }

    # ── Results by market regime ───────────────────────────────────────────────
    by_regime: dict[str, list] = defaultdict(list)
    for t in trades:
        r = t.get("market_regime", "NEUTRAL") or "NEUTRAL"
        by_regime[r].append(t.get("net_pnl_pln", 0) or 0)

    results_by_regime = {
        r: {
            "trades": len(pnls),
            "total_pnl_pln": round(sum(pnls), 2),
            "win_rate_pct": round(_safe_div(sum(1 for p in pnls if p > 0), len(pnls)) * 100, 1),
        }
        for r, pnls in by_regime.items()
    }

    # ── Results by score bucket ────────────────────────────────────────────────
    buckets = [(75, 80), (80, 85), (85, 90), (90, 101)]
    bucket_names = ["75-80", "80-85", "85-90", "90+"]
    by_bucket: dict[str, list] = defaultdict(list)
    for t in trades:
        sc = t.get("score", 0) or 0
        for (lo, hi), name in zip(buckets, bucket_names):
            if lo <= sc < hi:
                by_bucket[name].append(t.get("net_pnl_pln", 0) or 0)
                break

    results_by_score_bucket = {
        name: {
            "trades": len(pnls),
            "total_pnl_pln": round(sum(pnls), 2),
            "win_rate_pct": round(_safe_div(sum(1 for p in pnls if p > 0), len(pnls)) * 100, 1),
        }
        for name, pnls in by_bucket.items()
    }

    # ── Cost analysis ─────────────────────────────────────────────────────────
    cost_pct_of_gross = _safe_div(total_costs, abs(total_gross)) * 100 if total_gross else 0.0

    # ── P&L excluding top trades ──────────────────────────────────────────────
    sorted_pnls = sorted(net_pnls, reverse=True)
    pnl_ex_top5  = total_net - sum(sorted_pnls[:5])
    pnl_ex_top10 = total_net - sum(sorted_pnls[:10])

    # ── Signals per day (approximate) ─────────────────────────────────────────
    signals_per_day = trades_per_day  # we only track filled signals; use as proxy

    return {
        "total_return_pct": round(total_return_pct, 3),
        "net_pnl_pln": round(total_net, 2),
        "gross_pnl_pln": round(total_gross, 2),
        "total_costs_pln": round(total_costs, 2),
        "profit_factor": round(profit_factor, 3),
        "win_rate": round(win_rate, 2),
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "expectancy_pln": round(expectancy_pln, 2),
        "expectancy_r": round(expectancy_r, 3),
        "avg_win_pln": round(avg_win, 2),
        "avg_loss_pln": round(avg_loss, 2),
        "avg_win_loss_ratio": round(avg_win_loss, 3),
        "avg_holding_minutes": round(avg_hold, 1),
        "median_holding_minutes": round(med_hold, 1),
        "trades_per_day": round(trades_per_day, 2),
        "signals_per_day": round(signals_per_day, 2),
        "max_intraday_drawdown_pct": round(max_dd_pct * 100, 3),
        "max_daily_loss_pln": round(max_daily_loss, 2),
        "daily_loss_limit_hits": daily_loss_limit_hits,
        "max_consecutive_losses": max_consec_trades,
        "best_session_pnl": round(best_session, 2),
        "worst_session_pnl": round(worst_session, 2),
        "pct_profitable_sessions": round(pct_profitable_sessions, 2),
        "results_by_strategy": results_by_strategy,
        "results_by_hour": results_by_hour,
        "results_by_dow": results_by_dow,
        "results_by_regime": results_by_regime,
        "results_by_score_bucket": results_by_score_bucket,
        "cost_pct_of_gross": round(cost_pct_of_gross, 2),
        "pnl_ex_top5": round(pnl_ex_top5, 2),
        "pnl_ex_top10": round(pnl_ex_top10, 2),
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "sessions_count": sessions_count,
    }


def classify_strategy(
    strategy_name: str,
    trades: list[dict],
    cost_scenario: str = "BASE",
) -> str:
    """Classify strategy: FAIL, INCONCLUSIVE, PROMISING, or POTENTIAL_EDGE."""
    strat_trades = [t for t in trades if t.get("strategy") == strategy_name]
    if len(strat_trades) < 20:
        return "INCONCLUSIVE"

    pnls = [t.get("net_pnl_pln", 0) or 0 for t in strat_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = _safe_div(len(wins), len(pnls)) * 100
    pf = _safe_div(sum(wins), abs(sum(losses))) if losses else float("inf")
    total = sum(pnls)

    if total < 0 and pf < 1.0:
        return "FAIL"
    if pf < 1.2 or win_rate < 35:
        return "INCONCLUSIVE"
    if pf >= 1.5 and win_rate >= 45 and total > 0:
        return "POTENTIAL_EDGE"
    return "PROMISING"
