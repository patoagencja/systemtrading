"""Reset intraday paper trading data (trades, signals, snapshots, runs).
Does NOT touch swing data.
"""
import sys, os, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.database import db_cursor

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", "-y", action="store_true")
    parser.add_argument("--mode", choices=["live", "backtest"], default=None)
    args = parser.parse_args()

    force = args.force or os.getenv("RESET_FORCE") == "1"
    if not force:
        scope = f" ({args.mode})" if args.mode else " (all modes)"
        confirm = input(f"Delete all intraday data{scope}? Type YES: ")
        if confirm.strip().upper() != "YES":
            print("Aborted.")
            sys.exit(0)

    with db_cursor() as cur:
        tables = ["intraday_trades", "intraday_signals", "intraday_orders",
                  "intraday_snapshots", "intraday_runs"]
        for t in tables:
            if args.mode:
                cur.execute(f"DELETE FROM {t} WHERE run_mode=?", (args.mode,))
            else:
                cur.execute(f"DELETE FROM {t}")
    print("Intraday data reset complete.")
