"""
Intraday backtest.

Usage:
  python scripts/run_intraday_backtest.py --start 2025-01-01 --end 2026-06-19
  python scripts/run_intraday_backtest.py --start 2025-01-01  # end = today
"""
import sys, os, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from app.intraday.backtester import run_intraday_backtest
from app.intraday.reporting import generate_all_reports

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--interval", default="30m", help="Bar interval (default: 30m)")
    parser.add_argument("--min-score", type=float, default=75.0)
    parser.add_argument("--cost-scenario", default="BASE", choices=["LOW", "BASE", "STRESS"])
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()

    print(f"Running intraday backtest: {start} → {end}, interval={args.interval}, cost={args.cost_scenario}")
    results = run_intraday_backtest(
        start_date=start,
        end_date=end,
        interval=args.interval,
        cost_scenario=args.cost_scenario,
        min_score=args.min_score,
        verbose=True,
    )
    generate_all_reports(results.get("metrics", {}), results.get("trades", []), {
        "start": str(start), "end": str(end),
        "interval": args.interval, "cost_scenario": args.cost_scenario,
        "min_score": args.min_score,
        "effective_start": results.get("effective_start", str(start)),
        "effective_end": results.get("effective_end", str(end)),
    })
    print("Reports written to reports/intraday/")
