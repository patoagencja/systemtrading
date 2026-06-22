"""Live intraday scanner — called every 30 minutes by GitHub Actions."""
import datetime
import logging
import sys

from app.database import init_db
from app.intraday.execution import IntradayExecutionEngine
from app.intraday.session_manager import SessionManager, NY_TZ

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def run_intraday_scanner(run_mode: str = "live", verbose: bool = True) -> dict:
    """
    Main entry point called by GitHub Actions workflow every 30 minutes.
    Initialises DB, checks market status, then runs one bar scan.
    """
    # Ensure DB tables exist
    init_db()

    now_et = SessionManager.get_current_et_time()
    session_date = SessionManager.get_session_date(now_et)
    status = SessionManager.get_market_status(now_et)

    if verbose:
        log.info(f"Intraday scanner started | {now_et.strftime('%Y-%m-%d %H:%M:%S ET')} | status={status}")

    result = {
        "run_mode": run_mode,
        "session_date": session_date.isoformat(),
        "bar_timestamp": now_et.isoformat(),
        "market_status": status,
        "skipped": False,
        "skip_reason": "",
    }

    # Skip if market closed
    if status == "CLOSED":
        result["skipped"] = True
        result["skip_reason"] = "Market is closed"
        if verbose:
            log.info("Market closed — skipping scan")
        return result

    if status == "PRE-MARKET":
        result["skipped"] = True
        result["skip_reason"] = "Pre-market — no intraday signals"
        if verbose:
            log.info("Pre-market — skipping scan")
        return result

    # Determine if this is a force-close bar
    force_close = SessionManager.should_force_close(now_et)

    try:
        engine = IntradayExecutionEngine(run_mode=run_mode)
        scan_result = engine.run_scan(
            bar_timestamp=now_et,
            session_date=session_date,
            force_close=force_close,
        )
        result.update(scan_result)
        if verbose:
            log.info(
                f"Scan complete | signals={scan_result.get('signals_generated',0)} "
                f"filled={scan_result.get('orders_filled',0)} "
                f"closed={scan_result.get('positions_closed',0)}"
            )
    except Exception as e:
        log.exception(f"Scanner error: {e}")
        result["errors"] = [str(e)]

    return result


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"
    outcome = run_intraday_scanner(run_mode=mode, verbose=True)
    print(outcome)
