"""Reset all trading data (trades, signals, snapshots, stats) for a fresh start.

Interactive by default. Pass --force (or set RESET_FORCE=1) to skip confirmation,
e.g. when running in GitHub Actions / CI.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import reset_trading_data

if __name__ == "__main__":
    force = "--force" in sys.argv or "-y" in sys.argv or os.getenv("RESET_FORCE") == "1"

    if not force:
        confirm = input("This will DELETE all trades, signals, snapshots and stats. Type YES to confirm: ")
        if confirm.strip().upper() != "YES":
            print("Aborted.")
            sys.exit(0)

    reset_trading_data()
    print("Portfolio reset complete. Capital restored to initial value.")
