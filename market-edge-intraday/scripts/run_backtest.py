"""Run a historical backtest and write reports.

Usage:
    python scripts/run_backtest.py --start 2026-01-01 --end 2026-03-31 \\
        --strategies OPENING_RANGE_BREAKOUT,VWAP_MEAN_REVERSION \\
        --cost-scenario BASE --symbols AAPL,MSFT --min-score 75

If neither --symbols nor --universe is given, the configured universe is built.
The script refuses to fabricate results: if no data is available (no API key,
empty cache) it prints a clear message and exits non-zero.
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
    parser = argparse.ArgumentParser(
        description="Run an intraday backtest over real cached/provider data and write reports.",
    )
    parser.add_argument("--start", type=_parse_date, required=True, help="Start date YYYY-MM-DD.")
    parser.add_argument("--end", type=_parse_date, required=True, help="End date YYYY-MM-DD.")
    parser.add_argument(
        "--strategies",
        default="",
        help="Comma-separated strategy names. Default: configured enabled strategies.",
    )
    parser.add_argument(
        "--cost-scenario",
        choices=["LOW", "BASE", "STRESS"],
        default="BASE",
        help="Transaction-cost scenario. Default: BASE.",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--symbols", help="Comma-separated tickers to test.")
    src.add_argument("--universe", action="store_true", help="Use the built universe.")
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Minimum signal score (0-100). Default: configured MIN_SIGNAL_SCORE.",
    )
    parser.add_argument(
        "--out-dir",
        default="reports",
        help="Directory for generated report CSVs / summary. Default: reports.",
    )
    parser.add_argument("--log-level", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.backtest.engine import BacktestConfig, run_backtest
    from app.backtest.reports import generate_all_reports
    from app.config import settings
    from app.logging_config import configure_logging, get_logger

    configure_logging(args.log_level)
    logger = get_logger("scripts.run_backtest")

    symbols = _resolve_symbols(args, logger)
    if not symbols:
        print(
            "ERROR: no symbols to test.\n"
            "Provide --symbols AAPL,MSFT or --universe (which needs a data provider key).",
            file=sys.stderr,
        )
        return 2

    strategies = _resolve_strategies(args, settings)
    min_score = args.min_score if args.min_score is not None else settings.min_signal_score

    config = BacktestConfig(
        symbols=symbols,
        start=args.start,
        end=args.end,
        strategies=strategies,
        cost_scenario=args.cost_scenario,
        initial_capital_pln=settings.initial_capital_pln,
        min_score=min_score,
    )

    logger.info(
        "Backtest %s -> %s | %d symbols | strategies=%s | cost=%s | min_score=%s",
        args.start,
        args.end,
        len(symbols),
        strategies,
        args.cost_scenario,
        min_score,
    )

    try:
        result = run_backtest(config)
    except RuntimeError as exc:
        print(f"\nERROR: backtest could not run: {exc}", file=sys.stderr)
        print(
            "\nThis system never fabricates data. A backtest needs REAL historical bars.\n"
            "  - Add ALPACA_API_KEY / ALPACA_SECRET_KEY to .env, OR\n"
            "  - Pre-download data with scripts/download_historical_data.py.\n",
            file=sys.stderr,
        )
        return 3

    trades = getattr(result, "trades", []) or []
    metrics = getattr(result, "metrics", {}) or {}

    if not trades and not metrics:
        print(
            "\nERROR: the backtest produced no data (empty cache and/or no provider).\n"
            "Refusing to report fabricated results. Provide real data and retry.",
            file=sys.stderr,
        )
        return 3

    _print_metrics(metrics, len(trades))

    try:
        generate_all_reports(result, out_dir=args.out_dir)
        print(f"\nReports written to: {args.out_dir}/")
    except Exception as exc:  # pragma: no cover - reporting best-effort
        logger.warning("Report generation failed: %s", exc)
        print(f"WARNING: report generation failed: {exc}", file=sys.stderr)

    return 0


def _resolve_symbols(args: argparse.Namespace, logger) -> list[str]:
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not args.universe:
        return []
    from datetime import date as _date

    from app.data import get_provider
    from app.universe.builder import build_universe

    try:
        provider = get_provider()
    except RuntimeError as exc:
        logger.error("Cannot build universe without a provider: %s", exc)
        return []
    snapshot = build_universe(provider, _date.today())
    return _symbols_from_snapshot(snapshot)


def _symbols_from_snapshot(snapshot: object) -> list[str]:
    try:
        import pandas as pd

        if isinstance(snapshot, pd.DataFrame):
            return [str(s).upper() for s in snapshot.get("symbol", [])]
    except Exception:  # pragma: no cover
        pass
    seq = snapshot if isinstance(snapshot, (list, tuple)) else getattr(snapshot, "rows", [])
    out: list[str] = []
    for item in seq or []:
        sym = item.get("symbol") if isinstance(item, dict) else getattr(item, "symbol", None)
        if sym:
            out.append(str(sym).upper())
    return out


def _resolve_strategies(args: argparse.Namespace, settings) -> list[str]:
    if args.strategies.strip():
        return [s.strip() for s in args.strategies.split(",") if s.strip()]
    return [str(s) for s in settings.enabled_strategy_set]


def _print_metrics(metrics: dict, n_trades: int) -> None:
    print("\n" + "=" * 52)
    print("  BACKTEST METRICS")
    print("=" * 52)
    print(f"  {'trades':<28}{n_trades}")
    preferred = [
        "net_pnl_pln",
        "total_return_pct",
        "cagr",
        "win_rate",
        "profit_factor",
        "expectancy_r",
        "avg_r",
        "max_drawdown_pct",
        "sharpe",
        "sortino",
    ]
    seen = set()
    for key in preferred:
        if key in metrics:
            seen.add(key)
            print(f"  {key:<28}{_fmt(metrics[key])}")
    for key, value in metrics.items():
        if key not in seen and not isinstance(value, (dict, list)):
            print(f"  {key:<28}{_fmt(value)}")
    print("=" * 52)


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.4f}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
