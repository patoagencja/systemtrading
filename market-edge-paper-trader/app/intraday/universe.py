"""Intraday trading universe management."""
import json
import logging
import datetime
import time
from pathlib import Path

import pandas as pd
import numpy as np

from app.intraday.config import (
    INTRADAY_MIN_PRICE_USD,
    INTRADAY_MIN_AVG_DAILY_VOLUME,
    INTRADAY_MIN_AVG_DOLLAR_VOLUME_USD,
)

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
UNIVERSE_DIR = BASE_DIR / "data" / "intraday" / "universe"
UNIVERSE_FILE = UNIVERSE_DIR / "universe.json"

BASE_UNIVERSE = [
    # Mega-cap tech
    "AAPL","MSFT","NVDA","GOOGL","GOOG","META","AMZN","TSLA","AVGO","ORCL",
    "AMD","ADBE","CSCO","INTC","QCOM","TXN","MU","AMAT","LRCX","KLAC",
    "MRVL","ASML","TSM","SNPS","CDNS","ANSS","FTNT","PANW","CRWD","NET",
    "DDOG","SNOW","PLTR","UBER","LYFT","ABNB","DASH","ZM","SHOP","SQ",
    # Finance
    "JPM","BAC","WFC","GS","MS","C","AXP","BLK","SCHW","USB","PNC",
    "V","MA","PYPL","COF","DFS","SYF","ALLY","SOFI",
    # Healthcare
    "UNH","LLY","JNJ","ABBV","MRK","PFE","TMO","ABT","MDT","ISRG",
    "AMGN","GILD","BIIB","VRTX","REGN","MRNA","BMY","CVS","CI","HUM",
    # Consumer
    "HD","MCD","NKE","SBUX","TGT","WMT","COST","LOW","TJX",
    "BKNG","EXPE","MAR","HLT","MGM","WYNN","LVS",
    # Industrial/Energy
    "BA","CAT","DE","GE","HON","LMT","RTX","NOC","GD","UPS","FDX",
    "XOM","CVX","COP","SLB","HAL","MPC","VLO","PSX",
    # Communication
    "NFLX","DIS","CMCSA","WBD","T","VZ","TMUS",
    # REITs/Other
    "AMT","PLD","CCI","EQIX","SPG",
    # Liquid ETFs
    "SPY","QQQ","IWM","DIA","GLD","SLV","TLT","HYG","LQD","EEM",
    "XLK","XLF","XLE","XLV","XLI","XLC","XLY","XLP","XLU","XLRE","XLB",
    "SMH","SOXX","IBB","XBI","ARKK","ARKG","ARKW",
    "VXX","UVXY","SQQQ","TQQQ","SPXU","SPXL",
    "GDX","GDXJ","USO","UNG","IAU",
    "EWJ","EWZ","FXI","EWY","EWT",
    "IBIT","FBTC",
    # Additional liquid names
    "CRM","NOW","INTU","ADP","PAYX","WDAY","ZS","OKTA","MDB","GTLB",
    "ARM","SMCI","DELL","HPE","WDC","STX","KEYS","TRMB",
    "NXPI","ON","STM","WOLF","MPWR","ENPH","FSLR","SEDG",
    "COIN","MSTR",
    "HOOD","IBKR","ETSY","EBAY","PINS","SNAP",
    "ROKU","RIVN","LCID","NIO","LI","XPEV",
    "F","GM","TM","STLA",
    "AXON","TMDX","MEDP","PODD","DXCM","ALGN",
    "MO","PM","BTI","KO","PEP","MDLZ","GIS","CPB","SJM",
    "PG","CL","EL","ULTA",
    "BX","KKR","APO","ARES","OWL",
    "TSCO","ORLY","AZO",
    "DHI","LEN","PHM","TOL","NVR",
    "CBRE","JLL",
    "UL","NVO","SAP","SONY",
]

# Simple sector mapping for major tickers
SECTOR_MAP = {
    "AAPL":"Technology","MSFT":"Technology","NVDA":"Technology","GOOGL":"Technology",
    "GOOG":"Technology","META":"Technology","AMZN":"Consumer Discretionary",
    "TSLA":"Consumer Discretionary","AVGO":"Technology","ORCL":"Technology",
    "AMD":"Technology","ADBE":"Technology","CSCO":"Technology","INTC":"Technology",
    "QCOM":"Technology","TXN":"Technology","MU":"Technology","AMAT":"Technology",
    "LRCX":"Technology","KLAC":"Technology","MRVL":"Technology","ASML":"Technology",
    "TSM":"Technology","SNPS":"Technology","CDNS":"Technology","PANW":"Technology",
    "CRWD":"Technology","NET":"Technology","DDOG":"Technology","SNOW":"Technology",
    "PLTR":"Technology","UBER":"Technology","SHOP":"Technology","SQ":"Technology",
    "JPM":"Financials","BAC":"Financials","WFC":"Financials","GS":"Financials",
    "MS":"Financials","C":"Financials","AXP":"Financials","BLK":"Financials",
    "SCHW":"Financials","V":"Financials","MA":"Financials","PYPL":"Financials",
    "COF":"Financials","DFS":"Financials","BX":"Financials","KKR":"Financials",
    "UNH":"Health Care","LLY":"Health Care","JNJ":"Health Care","ABBV":"Health Care",
    "MRK":"Health Care","PFE":"Health Care","TMO":"Health Care","ABT":"Health Care",
    "MDT":"Health Care","ISRG":"Health Care","AMGN":"Health Care","GILD":"Health Care",
    "VRTX":"Health Care","REGN":"Health Care","MRNA":"Health Care","BMY":"Health Care",
    "CVS":"Health Care","CI":"Health Care","HUM":"Health Care",
    "HD":"Consumer Discretionary","MCD":"Consumer Discretionary",
    "NKE":"Consumer Discretionary","SBUX":"Consumer Discretionary",
    "TGT":"Consumer Discretionary","WMT":"Consumer Staples","COST":"Consumer Staples",
    "LOW":"Consumer Discretionary","TJX":"Consumer Discretionary",
    "BKNG":"Consumer Discretionary","EXPE":"Consumer Discretionary",
    "MAR":"Consumer Discretionary","HLT":"Consumer Discretionary",
    "BA":"Industrials","CAT":"Industrials","DE":"Industrials","GE":"Industrials",
    "HON":"Industrials","LMT":"Industrials","RTX":"Industrials","NOC":"Industrials",
    "GD":"Industrials","UPS":"Industrials","FDX":"Industrials",
    "XOM":"Energy","CVX":"Energy","COP":"Energy","SLB":"Energy",
    "HAL":"Energy","MPC":"Energy","VLO":"Energy","PSX":"Energy",
    "NFLX":"Communication Services","DIS":"Communication Services",
    "CMCSA":"Communication Services","T":"Communication Services",
    "VZ":"Communication Services","TMUS":"Communication Services",
    "WBD":"Communication Services",
    "AMT":"Real Estate","PLD":"Real Estate","CCI":"Real Estate",
    "EQIX":"Real Estate","SPG":"Real Estate",
    "KO":"Consumer Staples","PEP":"Consumer Staples","PG":"Consumer Staples",
    "MO":"Consumer Staples","PM":"Consumer Staples","WMT":"Consumer Staples",
    # ETFs
    "SPY":"ETF","QQQ":"ETF","IWM":"ETF","DIA":"ETF","GLD":"ETF","SLV":"ETF",
    "TLT":"ETF","HYG":"ETF","LQD":"ETF","EEM":"ETF",
    "XLK":"ETF","XLF":"ETF","XLE":"ETF","XLV":"ETF","XLI":"ETF","XLC":"ETF",
    "XLY":"ETF","XLP":"ETF","XLU":"ETF","XLRE":"ETF","XLB":"ETF",
    "SMH":"ETF","SOXX":"ETF","IBB":"ETF","XBI":"ETF","ARKK":"ETF","ARKG":"ETF",
    "ARKW":"ETF","VXX":"ETF","UVXY":"ETF","SQQQ":"ETF","TQQQ":"ETF",
    "SPXU":"ETF","SPXL":"ETF","GDX":"ETF","GDXJ":"ETF","USO":"ETF",
    "UNG":"ETF","IAU":"ETF","EWJ":"ETF","EWZ":"ETF","FXI":"ETF",
    "EWY":"ETF","EWT":"ETF","IBIT":"ETF","FBTC":"ETF",
}


class IntradayUniverse:
    """Manage universe of liquid US stocks/ETFs for intraday trading."""

    def __init__(self):
        UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)

    def build(self, max_tickers: int = 500) -> list[dict]:
        """Build/refresh universe. Returns list of {ticker, sector, name}."""
        tickers = list(dict.fromkeys(BASE_UNIVERSE))  # deduplicate, preserve order
        tickers = tickers[:max_tickers]

        universe = []
        for ticker in tickers:
            sector = SECTOR_MAP.get(ticker, "Unknown")
            universe.append({
                "ticker": ticker,
                "sector": sector,
                "name": ticker,
                "added_date": datetime.date.today().isoformat(),
            })

        self._save(universe)
        log.info(f"Universe built: {len(universe)} tickers")
        return universe

    def load(self) -> list[dict]:
        """Load universe from disk, build if missing."""
        if UNIVERSE_FILE.exists():
            try:
                with open(UNIVERSE_FILE) as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return data
            except Exception as e:
                log.warning(f"Failed to load universe: {e}")
        return self.build()

    def filter_for_session(
        self,
        tickers: list[str],
        daily_data: dict,
    ) -> list[str]:
        """Apply daily filters: price, volume, trend context."""
        filtered = []
        for ticker in tickers:
            df = daily_data.get(ticker)
            if df is None or df.empty or len(df) < 20:
                continue
            last = df.iloc[-1]
            price = last.get("close", 0)
            if price < INTRADAY_MIN_PRICE_USD:
                continue
            avg_vol = df["volume"].tail(20).mean()
            if avg_vol < INTRADAY_MIN_AVG_DAILY_VOLUME:
                continue
            avg_dv = (df["close"] * df["volume"]).tail(20).mean()
            if avg_dv < INTRADAY_MIN_AVG_DOLLAR_VOLUME_USD:
                continue
            filtered.append(ticker)
        return filtered

    def _save(self, universe: list[dict]):
        """Persist universe to JSON."""
        UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
        with open(UNIVERSE_FILE, "w") as f:
            json.dump(universe, f, indent=2)
        log.info(f"Universe saved to {UNIVERSE_FILE}")
