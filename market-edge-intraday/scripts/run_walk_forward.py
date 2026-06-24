"""Run a walk-forward analysis (rolling in-sample / out-of-sample) and summarise.

Usage:
    python scripts/run_walk_forward.py --start 2025-01-01 --end 2026-03-31 \\
        --strategies OPENING_RANGE_BREAKOUT --cost-scenario BASE --symbols AAPL,MSFT
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
        description="Walk-forward analysis over real data; reports out-of-sample robustness.",
    )
    parser.add_argument("--start", type=_parse_date, required=True, help="Start date YYYY-MM-DD.")
    parser.add_argument("--end", type=_parse_date, required=True, help="End date YYYY-MM-DD.")
    parser.add_argument("--strategies", default="", help="Comma-separated strategy names.")
    parser.add_argument(
        "--cost-scenario", choices=["LOW", "BASE", "STRESS"], default="BASE"
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--symbols", help="Comma-separated tickers.")
    src.add_argument("--universe", action="store_true", help="Use the built universe.")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--out-dir", default="reports")
    parser.add_argument("--log-level", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.backtest.engine import BacktestConfig
    from app.backtest.walk_forward import run_walk_forward
    from app.config import settings
    from app.logging_config import configure_logging, get_logger

    configure_logging(args.log_level)
    logger = get_logger("scripts.run_walk_forward")

    symbols = _resolve_symbols(args, logger)
    if not symbols:
        print(
            "ERROR: no symbols to test. Use --symbols or --universe (needs a provider key).",
            file=sys.stderr,
        )
        return 2

    strategies = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies.strip()
        else [str(s) for s in settings.enabled_strategy_set]
    )
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

    logger.info("Walk-forward %s -> %s | %d symbols", args.start, args.end, len(symbols))

    try:
        summary = run_walk_forward(config)
    except RuntimeError as exc:
        print(f"\nERROR: walk-forward could not run: {exc}", file=sys.stderr)
        print(
            "Real historical data is required. Add a provider key or pre-download bars.",
            file=sys.stderr,
        )
        return 3

    _print_summary(summary)
    _maybe_write(summary, args.out_dir, logger)
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


def _print_summary(summary: object) -> None:
    print("\n" + "=" * 52)
    print("  WALK-FORWARD SUMMARY")
    print("=" * 52)
    try:
        import pandas as pd

        if isinstance(summary, pd.DataFrame):
            print(summary.to_string(index=False))
            print("=" * 52)
            return
    except Exception:  # pragma: no cover
        pass
    if isinstance(summary, dict):
        for key, value in summary.items():
            if isinstance(value, (dict, list)):
                print(f"  {key}: {value}")
            else:
                print(f"  {key:<28}{value}")
    else:
        print(f"  {summary}")
    print("=" * 52)


def _maybe_write(summary: object, out_dir: str, logger) -> None:
    try:
        import os

        import pandas as pd

        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "walk_forward.csv")
        if isinstance(summary, pd.DataFrame):
            summary.to_csv(path, index=False)
        elif isinstance(summary, dict):
            pd.DataFrame([summary]).to_csv(path, index=False)
        else:
            return
        print(f"\nWalk-forward summary written to: {path}")
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not write walk-forward summary: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main())
