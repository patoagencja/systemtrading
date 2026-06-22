"""Reset all trading data (trades, signals, snapshots, stats) for a fresh start.

Interactive by default. Pass --force (or set RESET_FORCE=1) to skip confirmation,
e.g. when running in GitHub Actions / CI.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import reset_trading_data

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", "-y", action="store_true")
    parser.add_argument("--mode", choices=["live", "backtest"], default=None,
                        help="Only reset this run_mode (default: both)")
    args = parser.parse_args()

    force = args.force or os.getenv("RESET_FORCE") == "1"

    if not force:
        scope = f" ({args.mode} only)" if args.mode else ""
        confirm = input(f"This will DELETE all trades, signals, snapshots and stats{scope}. Type YES to confirm: ")
        if confirm.strip().upper() != "YES":
            print("Aborted.")
            sys.exit(0)

    reset_trading_data(run_mode=args.mode)
    scope = f" ({args.mode})" if args.mode else ""
    print(f"Portfolio reset complete{scope}. Capital restored to initial value.")
