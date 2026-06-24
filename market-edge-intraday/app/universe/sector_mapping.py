"""Static symbol -> sector and sector -> sector-ETF mappings.

A pragmatic, dependency-free mapping for common large-cap US names plus the
canonical SPDR sector ETFs. Unknown symbols resolve to ``"UNKNOWN"``, whose
sector ETF is the broad-market benchmark (SPY). Providers that supply sector
metadata should be preferred; this module is the fallback.
"""
from __future__ import annotations

#: Canonical sector -> representative sector ETF.
_SECTOR_ETF: dict[str, str] = {
    "TECHNOLOGY": "XLK",
    "FINANCIALS": "XLF",
    "ENERGY": "XLE",
    "HEALTHCARE": "XLV",
    "CONSUMER_DISCRETIONARY": "XLY",
    "CONSUMER_STAPLES": "XLP",
    "INDUSTRIALS": "XLI",
    "MATERIALS": "XLB",
    "UTILITIES": "XLU",
    "REAL_ESTATE": "XLRE",
    "COMMUNICATION_SERVICES": "XLC",
    "BROAD_MARKET": "SPY",
    "UNKNOWN": "SPY",
}

#: Common large-cap symbol -> sector. Not exhaustive; a fallback exists.
_SYMBOL_SECTOR: dict[str, str] = {
    # Technology
    "AAPL": "TECHNOLOGY", "MSFT": "TECHNOLOGY", "NVDA": "TECHNOLOGY",
    "AVGO": "TECHNOLOGY", "ORCL": "TECHNOLOGY", "ADBE": "TECHNOLOGY",
    "CRM": "TECHNOLOGY", "AMD": "TECHNOLOGY", "INTC": "TECHNOLOGY",
    "CSCO": "TECHNOLOGY", "QCOM": "TECHNOLOGY", "TXN": "TECHNOLOGY",
    "IBM": "TECHNOLOGY", "NOW": "TECHNOLOGY", "MU": "TECHNOLOGY",
    # Communication services
    "GOOGL": "COMMUNICATION_SERVICES", "GOOG": "COMMUNICATION_SERVICES",
    "META": "COMMUNICATION_SERVICES", "NFLX": "COMMUNICATION_SERVICES",
    "DIS": "COMMUNICATION_SERVICES", "T": "COMMUNICATION_SERVICES",
    "VZ": "COMMUNICATION_SERVICES", "CMCSA": "COMMUNICATION_SERVICES",
    # Consumer discretionary
    "AMZN": "CONSUMER_DISCRETIONARY", "TSLA": "CONSUMER_DISCRETIONARY",
    "HD": "CONSUMER_DISCRETIONARY", "MCD": "CONSUMER_DISCRETIONARY",
    "NKE": "CONSUMER_DISCRETIONARY", "LOW": "CONSUMER_DISCRETIONARY",
    "SBUX": "CONSUMER_DISCRETIONARY", "BKNG": "CONSUMER_DISCRETIONARY",
    # Consumer staples
    "WMT": "CONSUMER_STAPLES", "PG": "CONSUMER_STAPLES", "KO": "CONSUMER_STAPLES",
    "PEP": "CONSUMER_STAPLES", "COST": "CONSUMER_STAPLES", "MDLZ": "CONSUMER_STAPLES",
    # Financials
    "JPM": "FINANCIALS", "BAC": "FINANCIALS", "WFC": "FINANCIALS",
    "GS": "FINANCIALS", "MS": "FINANCIALS", "C": "FINANCIALS",
    "BRK.B": "FINANCIALS", "AXP": "FINANCIALS", "SCHW": "FINANCIALS",
    "BLK": "FINANCIALS", "SPGI": "FINANCIALS",
    # Healthcare
    "JNJ": "HEALTHCARE", "UNH": "HEALTHCARE", "LLY": "HEALTHCARE",
    "PFE": "HEALTHCARE", "MRK": "HEALTHCARE", "ABBV": "HEALTHCARE",
    "TMO": "HEALTHCARE", "ABT": "HEALTHCARE", "BMY": "HEALTHCARE",
    "AMGN": "HEALTHCARE", "GILD": "HEALTHCARE", "CVS": "HEALTHCARE",
    # Energy
    "XOM": "ENERGY", "CVX": "ENERGY", "COP": "ENERGY", "SLB": "ENERGY",
    "EOG": "ENERGY", "MPC": "ENERGY", "PSX": "ENERGY", "OXY": "ENERGY",
    # Industrials
    "BA": "INDUSTRIALS", "CAT": "INDUSTRIALS", "GE": "INDUSTRIALS",
    "HON": "INDUSTRIALS", "UPS": "INDUSTRIALS", "RTX": "INDUSTRIALS",
    "LMT": "INDUSTRIALS", "DE": "INDUSTRIALS", "UNP": "INDUSTRIALS",
    # Materials
    "LIN": "MATERIALS", "SHW": "MATERIALS", "FCX": "MATERIALS",
    "NEM": "MATERIALS", "APD": "MATERIALS",
    # Utilities
    "NEE": "UTILITIES", "DUK": "UTILITIES", "SO": "UTILITIES", "D": "UTILITIES",
    # Real estate
    "AMT": "REAL_ESTATE", "PLD": "REAL_ESTATE", "EQIX": "REAL_ESTATE",
    "SPG": "REAL_ESTATE", "O": "REAL_ESTATE",
    # ETFs (sector + broad)
    "SPY": "BROAD_MARKET", "VOO": "BROAD_MARKET", "IVV": "BROAD_MARKET",
    "QQQ": "TECHNOLOGY", "XLK": "TECHNOLOGY", "XLF": "FINANCIALS",
    "XLE": "ENERGY", "XLV": "HEALTHCARE", "XLY": "CONSUMER_DISCRETIONARY",
    "XLP": "CONSUMER_STAPLES", "XLI": "INDUSTRIALS", "XLB": "MATERIALS",
    "XLU": "UTILITIES", "XLRE": "REAL_ESTATE", "XLC": "COMMUNICATION_SERVICES",
}


def default_sector(symbol: str) -> str:
    """Return the mapped sector for ``symbol`` or ``"UNKNOWN"``."""
    return _SYMBOL_SECTOR.get(symbol.upper(), "UNKNOWN")


def sector_etf(sector: str | None) -> str:
    """Return the representative sector ETF for ``sector`` (SPY fallback)."""
    if not sector:
        return _SECTOR_ETF["UNKNOWN"]
    return _SECTOR_ETF.get(sector.upper(), _SECTOR_ETF["UNKNOWN"])


def all_sectors() -> list[str]:
    """All known sector names (excluding the UNKNOWN sentinel)."""
    return [s for s in _SECTOR_ETF if s != "UNKNOWN"]
