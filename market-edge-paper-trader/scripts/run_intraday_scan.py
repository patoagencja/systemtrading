"""Intraday scan — runs every 30 minutes during market hours.
Called by intraday-scan.yml workflow.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.intraday.scanner import run_intraday_scanner

if __name__ == "__main__":
    result = run_intraday_scanner(run_mode="live", verbose=True)
    print(f"Scan complete: {result}")
