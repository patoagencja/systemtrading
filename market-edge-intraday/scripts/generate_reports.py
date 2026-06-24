"""Generate analytics reports.

Two modes:
  - default: run a backtest over the given window, then write all reports.
  - --from-db: skip the backtest and summarise trades already stored in the DB
    (for the chosen run mode).

Usage:
    python scripts/generate_reports.py --start 2026-01-01 --end 2026-03-31 \\
        --symbols AAPL,MSFT
    python scripts/generate_reports.py --from-db --run-mode BACKTEST
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import sys
from datetime import date, datetime


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate analytics reports.")
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Summarise trades stored in the DB instead of running a new backtest.",
    )
    parser.add_argument(
        "--run-mode",
        choices=["BACKTEST", "LIVE_PAPER"],
        default="BACKTEST",
        help="Which stream to summarise when using --from-db. Default: BACKTEST.",
    )
    parser.add_argument("--start", type=_parse_date, help="Backtest start (non --from-db mode).")
    parser.add_argument("--end", type=_parse_date, help="Backtest end (non --from-db mode).")
    parser.add_argument("--strategies", default="")
    parser.add_argument("--cost-scenario", choices=["LOW", "BASE", "STRESS"], default="BASE")
    parser.add_argument("--symbols", help="Comma-separated tickers for the backtest mode.")
    parser.add_argument("--out-dir", default="reports")
    parser.add_argument("--log-level", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.logging_config import configure_logging, get_logger

    configure_logging(args.log_level)
    logger = get_logger("scripts.generate_reports")

    if args.from_db:
        return _from_db(args, logger)
    return _from_backtest(args, logger)


def _from_backtest(args: argparse.Namespace, logger) -> int:
    if not (args.start and args.end and args.symbols):
        print(
            "ERROR: backtest mode needs --start, --end and --symbols "
            "(or use --from-db to summarise stored trades).",
            file=sys.stderr,
        )
        return 2

    from app.backtest.engine import BacktestConfig, run_backtest
    from app.backtest.reports import generate_all_reports
    from app.config import settings

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    strategies = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies.strip()
        else [str(s) for s in settings.enabled_strategy_set]
    )
    config = BacktestConfig(
        symbols=symbols,
        start=args.start,
        end=args.end,
        strategies=strategies,
        cost_scenario=args.cost_scenario,
        initial_capital_pln=settings.initial_capital_pln,
        min_score=settings.min_signal_score,
    )
    try:
        result = run_backtest(config)
    except RuntimeError as exc:
        print(f"ERROR: backtest could not run: {exc}", file=sys.stderr)
        print("Real historical data is required; this system never fabricates results.")
        return 3

    if not getattr(result, "trades", None) and not getattr(result, "metrics", None):
        print("ERROR: backtest produced no data; refusing to write fabricated reports.")
        return 3

    generate_all_reports(result, out_dir=args.out_dir)
    print(f"OK: reports written to {args.out_dir}/")
    return 0


def _from_db(args: argparse.Namespace, logger) -> int:
    import os

    import pandas as pd

    from app.analytics.metrics import compute_metrics
    from app.database import session_scope
    from app.models import Trade

    rows: list[dict] = []
    with session_scope() as session:
        trades = (
            session.query(Trade)
            .filter(Trade.run_mode == args.run_mode, Trade.status == "CLOSED")
            .all()
        )
        for t in trades:
            rows.append(
                {
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "session_date": t.session_date,
                    "net_pnl_pln": t.net_pnl_pln,
                    "return_pct": t.return_pct,
                    "r_multiple": t.r_multiple,
                    "exit_reason": t.exit_reason,
                }
            )

    if not rows:
        print(f"No closed trades found for run mode {args.run_mode}.")
        return 0

    df = pd.DataFrame(rows)
    os.makedirs(args.out_dir, exist_ok=True)
    trades_path = os.path.join(args.out_dir, f"trades_{args.run_mode.lower()}.csv")
    df.to_csv(trades_path, index=False)

    try:
        metrics = compute_metrics(df)
    except Exception as exc:  # pragma: no cover - analytics best-effort
        logger.warning("compute_metrics failed (%s); writing raw trades only.", exc)
        metrics = {"trades": len(df)}

    metrics_path = os.path.join(args.out_dir, f"metrics_{args.run_mode.lower()}.csv")
    pd.DataFrame([metrics]).to_csv(metrics_path, index=False)
    print(f"OK: wrote {trades_path} and {metrics_path} ({len(df)} closed trades).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
