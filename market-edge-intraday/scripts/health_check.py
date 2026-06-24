"""System health check used by humans and by the docker healthcheck.

Checks:
  - database connectivity (a trivial query through session_scope)
  - market-data provider status / credentials
  - latest worker heartbeat (freshness)
  - latest market-data freshness (newest bar timestamp seen in snapshots)

Prints a small table and exits 0 (healthy) or 1 (unhealthy / degraded).

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --strict   # any warning -> non-zero
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report system health and exit 0/1.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures (exit non-zero).",
    )
    parser.add_argument("--log-level", default="WARNING")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.logging_config import configure_logging

    configure_logging(args.log_level)

    checks: list[Check] = [
        _check_database(),
        _check_provider(),
        _check_heartbeat(),
        _check_data_freshness(),
    ]

    _print_table(checks)

    failed = any(not c.ok and not c.warn for c in checks)
    warned = any(c.warn for c in checks)
    if failed:
        return 1
    if warned and args.strict:
        return 1
    return 0


def _check_database() -> Check:
    try:
        from sqlalchemy import text

        from app.database import session_scope

        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return Check("database", True, "reachable")
    except Exception as exc:
        return Check("database", False, f"unreachable: {exc}")


def _check_provider() -> Check:
    try:
        from app.config import settings

        if not settings.has_live_data_credentials:
            return Check(
                "data_provider",
                False,
                "no credentials (set ALPACA_API_KEY/ALPACA_SECRET_KEY)",
                warn=True,
            )
        return Check("data_provider", True, f"{settings.market_data_provider} configured")
    except Exception as exc:
        return Check("data_provider", False, f"error: {exc}")


def _check_heartbeat() -> Check:
    try:
        from app.database import session_scope
        from app.models import Heartbeat

        with session_scope() as session:
            hb = (
                session.query(Heartbeat)
                .order_by(Heartbeat.timestamp.desc())
                .first()
            )
        if hb is None:
            return Check("worker_heartbeat", False, "no heartbeat recorded yet", warn=True)
        age = _age_seconds(hb.timestamp)
        detail = f"{hb.service}={hb.status}, {age:.0f}s ago"
        # A worker that hasn't beaten in 30 min is stale (warn).
        return Check("worker_heartbeat", age < 1800, detail, warn=age >= 1800)
    except Exception as exc:
        return Check("worker_heartbeat", False, f"error: {exc}", warn=True)


def _check_data_freshness() -> Check:
    try:
        from app.config import settings
        from app.database import session_scope
        from app.models import PortfolioSnapshot

        with session_scope() as session:
            snap = (
                session.query(PortfolioSnapshot)
                .order_by(PortfolioSnapshot.timestamp.desc())
                .first()
            )
        if snap is None:
            return Check("data_freshness", True, "no snapshots yet (idle)", warn=False)
        age = _age_seconds(snap.timestamp)
        stale_after = max(settings.max_data_staleness_seconds, 1800)
        return Check(
            "data_freshness",
            age < stale_after,
            f"last snapshot {age:.0f}s ago",
            warn=age >= stale_after,
        )
    except Exception as exc:
        return Check("data_freshness", False, f"error: {exc}", warn=True)


def _age_seconds(ts: datetime | None) -> float:
    if ts is None:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


def _print_table(checks: list[Check]) -> None:
    print(f"\n{'check':<20}{'status':<10}detail")
    print("-" * 64)
    for c in checks:
        status = "OK" if c.ok else ("WARN" if c.warn else "FAIL")
        print(f"{c.name:<20}{status:<10}{c.detail}")
    print("-" * 64)


if __name__ == "__main__":
    raise SystemExit(main())
