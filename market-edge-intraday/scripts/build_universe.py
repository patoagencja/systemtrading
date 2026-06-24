"""Build the tradable universe for a given date and print the top names.

Usage:
    python scripts/build_universe.py --date 2026-06-24 --top 25
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
        description="Build the daily tradable universe via the configured data provider.",
    )
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=date.today(),
        help="As-of date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--target-size",
        type=int,
        default=None,
        help="Override the configured universe target size.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=25,
        help="How many top-ranked symbols to print. Default: 25.",
    )
    parser.add_argument("--log-level", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.data import get_provider
    from app.logging_config import configure_logging, get_logger
    from app.universe.builder import build_universe

    configure_logging(args.log_level)
    logger = get_logger("scripts.build_universe")

    try:
        provider = get_provider()
    except RuntimeError as exc:
        print(f"ERROR: market-data provider unavailable: {exc}", file=sys.stderr)
        print(
            "\nThis usually means no API key is configured.\n"
            "  1. Sign up free at https://alpaca.markets\n"
            "  2. Dashboard -> Paper Trading -> API Keys -> generate a key\n"
            "  3. Paste ALPACA_API_KEY and ALPACA_SECRET_KEY into your .env file\n",
            file=sys.stderr,
        )
        return 2

    logger.info("Building universe as of %s", args.date)
    try:
        snapshot = build_universe(provider, args.date, target_size=args.target_size)
    except RuntimeError as exc:
        print(f"ERROR: could not build universe: {exc}", file=sys.stderr)
        print(
            "If this is a credential error, add ALPACA_API_KEY / ALPACA_SECRET_KEY to .env.",
            file=sys.stderr,
        )
        return 2

    rows = _normalise_rows(snapshot)
    if not rows:
        print("No symbols qualified for the universe on this date.")
        return 0

    print(f"\nUniverse for {args.date} — {len(rows)} symbols (showing top {args.top}):\n")
    print(f"{'rank':>4}  {'symbol':<8}  {'price':>10}  {'$volume':>16}  sector")
    print("-" * 60)
    for row in rows[: args.top]:
        rank = row.get("rank", "")
        symbol = row.get("symbol", "")
        price = row.get("price")
        dvol = row.get("avg_dollar_volume")
        sector = row.get("sector") or ""
        price_s = f"{price:,.2f}" if isinstance(price, (int, float)) else ""
        dvol_s = f"{dvol:,.0f}" if isinstance(dvol, (int, float)) else ""
        print(f"{rank:>4}  {symbol:<8}  {price_s:>10}  {dvol_s:>16}  {sector}")
    return 0


def _normalise_rows(snapshot: object) -> list[dict]:
    """Accept either a list of dict-like rows, ORM objects, or a DataFrame."""
    try:
        import pandas as pd

        if isinstance(snapshot, pd.DataFrame):
            return snapshot.to_dict("records")
    except Exception:  # pragma: no cover - pandas always present
        pass

    rows: list[dict] = []
    seq = snapshot if isinstance(snapshot, (list, tuple)) else getattr(snapshot, "rows", [])
    for item in seq or []:
        if isinstance(item, dict):
            rows.append(item)
        else:
            rows.append(
                {
                    "rank": getattr(item, "rank", None),
                    "symbol": getattr(item, "symbol", None),
                    "price": getattr(item, "price", None),
                    "avg_dollar_volume": getattr(item, "avg_dollar_volume", None),
                    "sector": getattr(item, "sector", None),
                }
            )
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
