"""Live paper trading scan — fetch latest data, update positions, open new ones."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from app.scanner import run_scanner
from app.reporting import print_terminal_report

if __name__ == "__main__":
    today = date.today()
    print(f"Starting live paper scan for {today}...")
    stats = run_scanner(today=today, verbose=True)
    print_terminal_report(stats)
