"""EOD paper trading scan — runs after US market close (20:30 UTC).

Closes any remaining open positions using today's EOD data, then scans
the watchlist and generates new signals for next-day entry.
Pending signals are entered the following morning via run_morning_entry.py.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from app.scanner import run_scanner
from app.reporting import print_terminal_report

if __name__ == "__main__":
    today = date.today()
    print(f"Starting EOD scan for {today}...")
    # skip_pending_entry=True: morning-entry.yml handles entries separately
    stats = run_scanner(today=today, verbose=True, skip_pending_entry=True)
    print_terminal_report(stats)
