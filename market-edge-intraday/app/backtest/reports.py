"""Report generation — turns a backtest result into CSVs and a markdown summary.

Everything is derived from the actual trades/equity; nothing is invented. If the
sample is too small the summary says INCONCLUSIVE rather than over-claiming.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pandas as pd

from app.analytics import attribution
from app.analytics.metrics import classify_strategy
from app.backtest.engine import BacktestResult
from app.logging_config import get_logger

logger = get_logger(__name__)


def _write_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index=True)
    logger.info("Wrote %s (%d rows)", path, len(df))


def generate_all_reports(
    result: BacktestResult,
    out_dir: str = "reports",
    *,
    walk_forward=None,
    cost_scenarios: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Write all report artefacts. Returns a map of report-name -> file path."""
    os.makedirs(out_dir, exist_ok=True)
    trades = result.trades
    written: dict[str, str] = {}

    # Strategy comparison.
    strat_rows = []
    for name, m in result.per_strategy.items():
        depends_top5 = (
            m.get("result_without_top5", 0.0) <= 0 and m.get("net_pnl", 0.0) > 0
        )
        status = classify_strategy(
            num_trades=m.get("num_trades", 0),
            profit_factor_base=m.get("profit_factor", 0.0),
            net_pnl=m.get("net_pnl", 0.0),
            oos_positive=bool(result.metrics.get("net_pnl", 0) > 0),
            walk_forward_positive_share=(walk_forward.summary.get("share_positive", 0.0)
                                         if walk_forward else 0.0),
            max_drawdown=m.get("max_drawdown", 0.0),
            depends_on_top5=depends_top5,
            stress_positive=(cost_scenarios.get("STRESS", {}).get("net_pnl", 0.0) > 0
                             if cost_scenarios else m.get("net_pnl", 0.0) > 0),
        )
        strat_rows.append({
            "strategy": name,
            "num_trades": m.get("num_trades", 0),
            "win_rate": round(m.get("win_rate", 0.0), 3),
            "profit_factor": round(m.get("profit_factor", 0.0), 3),
            "expectancy_r": round(m.get("expectancy_r", 0.0), 3),
            "net_pnl_pln": round(m.get("net_pnl", 0.0), 2),
            "avg_holding_bars": round(m.get("avg_holding_bars", 0.0), 2),
            "status": status.value,
        })
    strat_df = pd.DataFrame(strat_rows).set_index("strategy") if strat_rows else pd.DataFrame()
    _write_csv(strat_df, os.path.join(out_dir, "strategy_comparison.csv"))
    written["strategy_comparison"] = os.path.join(out_dir, "strategy_comparison.csv")

    # Attribution CSVs.
    sector_lookup = {}
    if trades:
        from app.universe.sector_mapping import default_sector

        sector_lookup = {t.symbol: default_sector(t.symbol) for t in trades}

    for name, fn, kwargs in [
        ("yearly_results", attribution.pnl_by_year, {}),
        ("monthly_results", attribution.pnl_by_month, {}),
        ("hourly_results", attribution.pnl_by_hour, {}),
        ("sector_results", attribution.pnl_by_sector, {"sector_lookup": sector_lookup}),
    ]:
        try:
            df = fn(trades, **kwargs)
        except Exception as exc:  # noqa: BLE001 - reports must not crash a run
            logger.warning("attribution %s failed: %s", name, exc)
            df = pd.DataFrame()
        _write_csv(df, os.path.join(out_dir, f"{name}.csv"))
        written[name] = os.path.join(out_dir, f"{name}.csv")

    # Cost scenarios.
    if cost_scenarios:
        cs_df = pd.DataFrame(cost_scenarios).T
        _write_csv(cs_df, os.path.join(out_dir, "cost_scenarios.csv"))
        written["cost_scenarios"] = os.path.join(out_dir, "cost_scenarios.csv")

    # Walk-forward / OOS.
    if walk_forward is not None:
        if walk_forward.windows_table is not None:
            _write_csv(walk_forward.windows_table, os.path.join(out_dir, "walk_forward.csv"))
            written["walk_forward"] = os.path.join(out_dir, "walk_forward.csv")
        oos_df = pd.DataFrame(walk_forward.splits).T
        _write_csv(oos_df, os.path.join(out_dir, "out_of_sample.csv"))
        written["out_of_sample"] = os.path.join(out_dir, "out_of_sample.csv")

    # Equity curve.
    if result.equity_curve is not None and not result.equity_curve.empty:
        result.equity_curve.to_csv(os.path.join(out_dir, "equity_curve.csv"), index=False)
        written["equity_curve"] = os.path.join(out_dir, "equity_curve.csv")

    # Markdown summary.
    summary_path = os.path.join(out_dir, "backtest_summary.md")
    with open(summary_path, "w") as f:
        f.write(_summary_markdown(result, strat_df, walk_forward, cost_scenarios))
    written["backtest_summary"] = summary_path

    # Machine-readable metrics dump.
    with open(os.path.join(out_dir, "metrics.json"), "w") as f:
        json.dump({k: _jsonable(v) for k, v in result.metrics.items()}, f, indent=2)
    return written


def _df_to_md(df: pd.DataFrame) -> str:
    """Minimal markdown table (avoids the optional 'tabulate' dependency)."""
    if df is None or df.empty:
        return "_(no data)_"
    df = df.reset_index()
    headers = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in df.columns) + " |")
    return "\n".join(lines)


def _jsonable(v):
    try:
        if v != v:  # NaN
            return None
    except Exception:  # noqa: BLE001
        pass
    return v


def _summary_markdown(result, strat_df, walk_forward, cost_scenarios) -> str:
    m = result.metrics
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rng = result.data_range
    lines = [
        "# Backtest summary",
        "",
        f"_Generated {now}. Run mode: {result.run_mode}. PAPER ONLY — no real orders._",
        "",
        "## Data",
        f"- Date range: **{rng[0]} → {rng[1]}**" if rng else "- Date range: n/a",
        f"- Symbols: {len(result.config.symbols)}",
        f"- Cost scenario: {result.config.cost_scenario}",
        f"- Min signal score: {result.config.min_score}",
        "",
        "## Headline metrics",
        f"- Trades: **{m.get('num_trades', 0)}**",
        f"- Net P&L (PLN): **{m.get('net_pnl', 0.0):,.0f}**",
        f"- Total return: **{m.get('total_return', 0.0) * 100:.2f}%**",
        f"- Profit factor: **{m.get('profit_factor', 0.0):.2f}**",
        f"- Win rate: **{m.get('win_rate', 0.0) * 100:.1f}%**",
        f"- Expectancy (R): **{m.get('expectancy_r', 0.0):.3f}**",
        f"- Max drawdown: **{m.get('max_drawdown', 0.0) * 100:.2f}%**",
        f"- Sharpe: **{m.get('sharpe', 0.0):.2f}**  |  Sortino: {m.get('sortino', 0.0):.2f}",
        f"- Result without top 5 trades (PLN): {m.get('result_without_top5', 0.0):,.0f}",
        "",
    ]
    if result.inconclusive:
        lines += [
            "> ⚠️ **INCONCLUSIVE** — fewer than 100 trades. This sample is too small "
            "to judge whether the strategies have a real edge. Gather more data.",
            "",
        ]
    if not strat_df.empty:
        lines += ["## Per-strategy", "", _df_to_md(strat_df), ""]
    if walk_forward is not None:
        lines += ["## Walk-forward", "", f"```\n{json.dumps(walk_forward.summary, indent=2)}\n```", ""]
    if cost_scenarios:
        lines += ["## Cost scenarios", "", _df_to_md(pd.DataFrame(cost_scenarios).T), ""]
    lines += [
        "## Reminder",
        "- One backtest is not proof of an edge. Look at out-of-sample, walk-forward "
        "stability, and survival under the STRESS cost scenario.",
        "- A profitable month means nothing on its own.",
    ]
    return "\n".join(lines)
