import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "database.sqlite"
WATCHLIST_PATH = DATA_DIR / "watchlist.csv"

load_dotenv(BASE_DIR / ".env")

INITIAL_CAPITAL_PLN = float(os.getenv("INITIAL_CAPITAL_PLN", 1_000_000))
PLN_USD_RATE = float(os.getenv("PLN_USD_RATE", 4.00))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", 0.005))
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", 0.03))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 40))
MAX_PORTFOLIO_EXPOSURE_PCT = float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", 0.80))
MIN_SCORE_TO_OPEN = float(os.getenv("MIN_SCORE_TO_OPEN", 75))
MAX_HOLDING_DAYS = int(os.getenv("MAX_HOLDING_DAYS", 10))
DATA_PERIOD = os.getenv("DATA_PERIOD", "2y")
DATA_INTERVAL = os.getenv("DATA_INTERVAL", "1d")
MIN_HISTORY_BARS = 220
MIN_AVG_VOLUME = int(os.getenv("MIN_AVG_VOLUME", 500_000))  # skip illiquid tickers

# Realistic trading costs applied on every open and close
# COMMISSION_PCT: broker fee per side as % of trade value (0.001 = 0.1%)
#   Interactive Brokers ~0.05%, Degiro ~0.1%, Saxo ~0.15%
COMMISSION_PCT = float(os.getenv("COMMISSION_PCT", 0.001))   # 0.10% per side

# SLIPPAGE_PCT: price impact per side as % (market order vs last close)
#   Liquid US large-caps: ~0.05%; mid-caps / volatile: ~0.10%
SLIPPAGE_PCT   = float(os.getenv("SLIPPAGE_PCT",   0.0005))  # 0.05% per side

STRATEGY_MAX_HOLDING = {
    "MEAN_REVERSION_UPTREND": 7,
    "MOMENTUM_BREAKOUT": 10,
    "PULLBACK_TREND": 12,
    "ETF_RELATIVE_STRENGTH": 10,
}

SECTOR_ETFS = {
    "tech": "XLK",
    "semi": "SOXX",
    "energy": "XLE",
    "finance": "XLF",
    "industrial": "XLI",
    "broad": "SPY",
}
