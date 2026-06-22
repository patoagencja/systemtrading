"""Performance metrics: Sharpe, Sortino, Calmar, expectancy, streaks, monthly returns."""
import math

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE_ANNUAL = 0.04  # 4% annualised (approx. US T-bill 2024)


def compute_metrics(snapshots: list[dict], trades: list[dict], initial_capital: float) -> dict:
    """Return comprehensive performance metrics dict from DB snapshots + trades."""
    if not snapshots:
        return _empty_metrics()

    values = [s["total_value_pln"] for s in snapshots]
    n = len(values)
    final = values[-1]

    # ── daily returns ───────────────────────────────────────────────────────
    daily_returns = []
    for i in range(1, n):
        if values[i - 1] > 0:
            daily_returns.append((values[i] - values[i - 1]) / values[i - 1])

    # ── basic ───────────────────────────────────────────────────────────────
    total_return_pct = (final / initial_capital - 1) * 100
    years = n / TRADING_DAYS_PER_YEAR
    ann_return_pct = ((final / initial_capital) ** (1 / max(years, 1 / TRADING_DAYS_PER_YEAR)) - 1) * 100

    # ── drawdown series & max DD ─────────────────────────────────────────────
    peak = values[0]
    max_dd_pct = 0.0
    dd_series = []
    for v in values:
        peak = max(peak, v)
        dd = (v - peak) / peak * 100
        dd_series.append(dd)
        max_dd_pct = min(max_dd_pct, dd)

    # ── Sharpe ───────────────────────────────────────────────────────────────
    sharpe = 0.0
    if len(daily_returns) > 1:
        mean_r = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_r = math.sqrt(var) if var > 0 else 0.0
        rf_daily = (1 + RISK_FREE_RATE_ANNUAL) ** (1 / TRADING_DAYS_PER_YEAR) - 1
        if std_r > 0:
            sharpe = (mean_r - rf_daily) / std_r * math.sqrt(TRADING_DAYS_PER_YEAR)

    # ── Sortino ──────────────────────────────────────────────────────────────
    sortino = 0.0
    if len(daily_returns) > 1:
        rf_daily = (1 + RISK_FREE_RATE_ANNUAL) ** (1 / TRADING_DAYS_PER_YEAR) - 1
        mean_r = sum(daily_returns) / len(daily_returns)
        downside = [r for r in daily_returns if r < rf_daily]
        if downside:
            down_var = sum(r ** 2 for r in downside) / len(downside)
            down_std = math.sqrt(down_var)
            if down_std > 0:
                sortino = (mean_r - rf_daily) * TRADING_DAYS_PER_YEAR / (down_std * math.sqrt(TRADING_DAYS_PER_YEAR))

    # ── Calmar ───────────────────────────────────────────────────────────────
    calmar = ann_return_pct / abs(max_dd_pct) if max_dd_pct < 0 else float("inf")

    # ── trade metrics ─────────────────────────────────────────────────────────
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = [t for t in closed if (t.get("pnl_pln") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_pln") or 0) <= 0]

    total_tr = len(closed)
    win_rate = len(wins) / total_tr * 100 if total_tr else 0.0
    avg_win = sum(t["pnl_pln"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t["pnl_pln"] for t in losses) / len(losses)) if losses else 0.0
    gross_profit = sum(t["pnl_pln"] for t in wins)
    gross_loss = abs(sum(t["pnl_pln"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    wr_frac = len(wins) / total_tr if total_tr else 0
    expectancy = wr_frac * avg_win - (1 - wr_frac) * avg_loss
    r_multiples = [t.get("r_multiple") or 0 for t in closed]
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
    hold_days = [t.get("holding_days") or 0 for t in closed]
    avg_hold = sum(hold_days) / len(hold_days) if hold_days else 0.0

    # ── streaks ───────────────────────────────────────────────────────────────
    max_win_streak = max_loss_streak = 0
    cur_streak = 0
    cur_type = None
    for t in sorted(closed, key=lambda x: x.get("exit_date") or ""):
        is_win = (t.get("pnl_pln") or 0) > 0
        if cur_type is None or is_win != cur_type:
            cur_streak = 1
            cur_type = is_win
        else:
            cur_streak += 1
        if is_win:
            max_win_streak = max(max_win_streak, cur_streak)
        else:
            max_loss_streak = max(max_loss_streak, cur_streak)

    # ── monthly returns (last value per month) ────────────────────────────────
    month_last: dict[str, float] = {}
    for s in snapshots:
        key = s["snapshot_date"][:7]
        month_last[key] = s["total_value_pln"]

    monthly_returns: dict[str, float] = {}
    prev_val = initial_capital
    for k in sorted(month_last.keys()):
        v = month_last[k]
        monthly_returns[k] = (v - prev_val) / prev_val * 100 if prev_val > 0 else 0.0
        prev_val = v

    # ── exit reasons ─────────────────────────────────────────────────────────
    exit_reasons: dict[str, int] = {}
    for t in closed:
        r = t.get("exit_reason") or "unknown"
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    return {
        "total_return_pct": total_return_pct,
        "ann_return_pct": ann_return_pct,
        "max_drawdown_pct": max_dd_pct,
        "dd_series": dd_series,
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3) if calmar != float("inf") else 999.0,
        "total_trades": total_tr,
        "win_trades": len(wins),
        "loss_trades": len(losses),
        "win_rate": win_rate,
        "avg_win_pln": avg_win,
        "avg_loss_pln": avg_loss,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else 999.0,
        "expectancy_pln": expectancy,
        "avg_r_multiple": avg_r,
        "avg_holding_days": avg_hold,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "monthly_returns": monthly_returns,
        "exit_reasons": exit_reasons,
        "n_days": n,
        "values": values,
        "dates": [s["snapshot_date"] for s in snapshots],
    }


def _empty_metrics() -> dict:
    return {
        "total_return_pct": 0.0, "ann_return_pct": 0.0,
        "max_drawdown_pct": 0.0, "dd_series": [],
        "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
        "total_trades": 0, "win_trades": 0, "loss_trades": 0,
        "win_rate": 0.0, "avg_win_pln": 0.0, "avg_loss_pln": 0.0,
        "gross_profit": 0.0, "gross_loss": 0.0, "profit_factor": 0.0,
        "expectancy_pln": 0.0, "avg_r_multiple": 0.0, "avg_holding_days": 0.0,
        "max_win_streak": 0, "max_loss_streak": 0,
        "monthly_returns": {}, "exit_reasons": {},
        "n_days": 0, "values": [], "dates": [],
    }
