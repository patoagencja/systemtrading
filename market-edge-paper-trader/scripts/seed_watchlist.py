"""Load watchlist.csv into the database."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import csv
from app.config import WATCHLIST_PATH
from app.database import db_cursor

if __name__ == "__main__":
    if not WATCHLIST_PATH.exists():
        print(f"ERROR: {WATCHLIST_PATH} not found. Run from project root.")
        sys.exit(1)

    with db_cursor() as cur:
        cur.execute("DELETE FROM watchlist")
        count = 0
        with open(WATCHLIST_PATH, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cur.execute("""
                    INSERT OR IGNORE INTO watchlist (ticker, sector, is_etf, sector_etf, active)
                    VALUES (?, ?, ?, ?, 1)
                """, (
                    row["ticker"].strip().upper(),
                    row.get("sector", "").strip(),
                    int(row.get("is_etf", 0)),
                    row.get("sector_etf", "").strip() or None,
                ))
                count += 1

    print(f"Seeded {count} tickers into watchlist.")
