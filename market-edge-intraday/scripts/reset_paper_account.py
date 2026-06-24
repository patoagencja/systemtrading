"""Reset LIVE_PAPER trading state (trades, positions, signals, snapshots).

Usage:
    python scripts/reset_paper_account.py --yes

Without --yes you will be asked to confirm. By default this clears only the
LIVE_PAPER stream and leaves BACKTEST data intact. Use --all to drop and
recreate every table (destructive, removes backtest data too).
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset paper-trading data. Defaults to LIVE_PAPER stream only.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Drop and recreate the ENTIRE schema (also wipes BACKTEST data).",
    )
    parser.add_argument("--log-level", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.logging_config import configure_logging, get_logger

    configure_logging(args.log_level)
    logger = get_logger("scripts.reset_paper_account")

    scope = "ALL data (backtest + live paper)" if args.all else "LIVE_PAPER data only"
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to reset without --yes in a non-interactive shell.", file=sys.stderr)
            return 2
        answer = input(f"This will permanently delete {scope}. Continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0

    if args.all:
        from app.database import reset_db

        reset_db()
        print("OK: entire schema dropped and recreated.")
        return 0

    deleted = _reset_live_paper(logger)
    print(f"OK: cleared LIVE_PAPER stream ({deleted} rows removed across tables).")
    return 0


def _reset_live_paper(logger) -> int:
    """Delete LIVE_PAPER rows from the run-mode-scoped tables; keep schema/backtest."""
    from app.database import init_db, session_scope
    from app.enums import RunMode
    from app.models import (
        Order,
        PortfolioSnapshot,
        Position,
        Signal,
        Trade,
        TradingSession,
    )

    init_db()  # ensure tables exist before deleting

    mode = str(RunMode.LIVE_PAPER)
    total = 0
    with session_scope() as session:
        for model in (Order, Position, Trade, Signal, PortfolioSnapshot, TradingSession):
            count = session.query(model).filter(model.run_mode == mode).delete(
                synchronize_session=False
            )
            total += count or 0
            logger.info("Deleted %s LIVE_PAPER rows from %s", count, model.__tablename__)
    return total


if __name__ == "__main__":
    raise SystemExit(main())
