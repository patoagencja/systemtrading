"""Reset all trading data (trades, signals, snapshots, stats) for a fresh start."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import db_cursor

if __name__ == "__main__":
    confirm = input("This will DELETE all trades, signals, snapshots and stats. Type YES to confirm: ")
    if confirm.strip().upper() != "YES":
        print("Aborted.")
        sys.exit(0)

    with db_cursor() as cur:
        cur.execute("DELETE FROM trades")
        cur.execute("DELETE FROM signals")
        cur.execute("DELETE FROM portfolio_snapshots")
        cur.execute("DELETE FROM strategy_stats")

    print("Portfolio reset complete. Capital restored to initial value.")
