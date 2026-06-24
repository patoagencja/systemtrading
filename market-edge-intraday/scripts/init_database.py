"""Create the database schema (idempotent).

Usage:
    python scripts/init_database.py
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create all database tables (idempotent). Safe to run repeatedly.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override log level (DEBUG/INFO/WARNING/ERROR).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.database import get_engine, init_db
    from app.logging_config import configure_logging, get_logger

    configure_logging(args.log_level)
    logger = get_logger("scripts.init_database")

    try:
        init_db()
    except Exception as exc:  # pragma: no cover - operational guard
        logger.error("Failed to initialise database: %s", exc)
        print(f"ERROR: could not initialise database: {exc}", file=sys.stderr)
        print(
            "Hint: make sure PostgreSQL is reachable (docker compose up -d postgres) "
            "or set INTRADAY_TEST_DB_URL=sqlite:///./local.db for a local file DB.",
            file=sys.stderr,
        )
        return 1

    target = str(get_engine().url).split("@")[-1]
    print(f"OK: database schema ensured ({target}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
