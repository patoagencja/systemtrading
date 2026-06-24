"""Start the LIVE PAPER trading loop (virtual capital only, NO real orders).

Usage:
    python scripts/run_live_paper.py

This starts the APScheduler-driven engine and blocks until interrupted. It never
sends a real broker order: all fills are simulated against live market data.
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the paper-trading engine loop (PAPER ONLY, no real orders).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation (for containers / CI).",
    )
    parser.add_argument("--log-level", default=None)
    return parser


_BANNER = (
    "\n"
    "============================================================\n"
    "  PAPER TRADING — VIRTUAL CAPITAL ONLY\n"
    "  No real orders are ever sent. No real money is at risk.\n"
    "  This is a research / simulation engine using live data.\n"
    "============================================================\n"
)


def main() -> int:
    args = build_parser().parse_args()

    from app.config import settings
    from app.logging_config import configure_logging, get_logger

    configure_logging(args.log_level)
    logger = get_logger("scripts.run_live_paper")

    print(_BANNER)

    if not settings.has_live_data_credentials:
        print(
            "ERROR: no market-data credentials configured.\n"
            "  Add ALPACA_API_KEY and ALPACA_SECRET_KEY to your .env\n"
            "  (free key from https://alpaca.markets -> Paper Trading -> API Keys).\n",
            file=sys.stderr,
        )
        return 2

    if not args.yes and sys.stdin.isatty():
        answer = input("Start the live PAPER loop now? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0

    from app.engine.live_engine import run_live_paper

    logger.info("Starting live paper engine loop (Ctrl-C to stop).")
    try:
        run_live_paper()
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    except Exception as exc:  # pragma: no cover - operational guard
        logger.error("Live paper engine crashed: %s", exc)
        print(f"ERROR: live paper engine crashed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
