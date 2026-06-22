"""Build/refresh the intraday universe (filtered list of liquid tickers)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.intraday.universe import IntradayUniverse

if __name__ == "__main__":
    print("Building intraday universe...")
    universe = IntradayUniverse()
    tickers = universe.build()
    print(f"Universe built: {len(tickers)} tickers saved to data/intraday/universe/")
