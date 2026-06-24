"""Canonical enumerations shared across the whole system.

Keeping every status / reason string in one place avoids typos and makes the
database, strategies, broker and dashboard agree on a single vocabulary.
"""
from __future__ import annotations

from enum import StrEnum  # Python 3.11+: str-backed enum, serialises cleanly to JSON/SQL


class RunMode(StrEnum):
    """Separates historical simulation from live paper trading.

    The dashboard and the database must never mix these two streams.
    """

    BACKTEST = "BACKTEST"
    LIVE_PAPER = "LIVE_PAPER"


class StrategyName(StrEnum):
    OPENING_RANGE_BREAKOUT = "OPENING_RANGE_BREAKOUT"
    VWAP_MEAN_REVERSION = "VWAP_MEAN_REVERSION"
    RELATIVE_STRENGTH_MOMENTUM = "RELATIVE_STRENGTH_MOMENTUM"
    VOLUME_EXPANSION_MOMENTUM = "VOLUME_EXPANSION_MOMENTUM"


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"  # reserved; V1 is long-only


class OrderType(StrEnum):
    MARKET = "MARKET"
    STOP = "STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIME_EXIT = "TIME_EXIT"
    EOD_EXIT = "EOD_EXIT"


class SignalStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TradeStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ExitReason(StrEnum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIME_STOP = "TIME_STOP"
    INVALIDATION = "INVALIDATION"
    END_OF_DAY_FLATTEN = "END_OF_DAY_FLATTEN"
    KILL_SWITCH = "KILL_SWITCH"
    TRAILING_STOP = "TRAILING_STOP"


class RejectionReason(StrEnum):
    GAP_TOO_LARGE = "GAP_TOO_LARGE"
    OPEN_BELOW_STOP = "OPEN_BELOW_STOP"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    RISK_REWARD_DEGRADED = "RISK_REWARD_DEGRADED"
    STALE_DATA = "STALE_DATA"
    LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    PORTFOLIO_RISK_LIMIT = "PORTFOLIO_RISK_LIMIT"
    SECTOR_LIMIT = "SECTOR_LIMIT"
    DUPLICATE_TICKER = "DUPLICATE_TICKER"
    ENTRY_TOO_LATE = "ENTRY_TOO_LATE"
    SCORE_TOO_LOW = "SCORE_TOO_LOW"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    MAX_POSITIONS = "MAX_POSITIONS"
    NOT_IN_UNIVERSE = "NOT_IN_UNIVERSE"
    ZERO_OR_NEGATIVE_SIZE = "ZERO_OR_NEGATIVE_SIZE"
    INCOMPLETE_BAR = "INCOMPLETE_BAR"


class SessionStatus(StrEnum):
    PENDING = "PENDING"
    PREMARKET = "PREMARKET"
    RUNNING = "RUNNING"
    CLOSING = "CLOSING"
    COMPLETED = "COMPLETED"
    HALTED = "HALTED"  # kill switch


class CostScenario(StrEnum):
    LOW = "LOW"
    BASE = "BASE"
    STRESS = "STRESS"


class Severity(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AssetType(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    ETF = "ETF"


class MarketStatus(StrEnum):
    CLOSED = "CLOSED"
    PREMARKET = "PREMARKET"
    OPEN = "OPEN"
    AFTERHOURS = "AFTERHOURS"


class StrategyStatus(StrEnum):
    """Research verdict for a strategy after backtesting."""

    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    PROMISING = "PROMISING"
    POTENTIAL_EDGE = "POTENTIAL_EDGE"


# Per-side commission / slippage by scenario (as fraction of notional).
COST_TABLE = {
    CostScenario.LOW: {"commission": 0.0001, "slippage": 0.0001},
    CostScenario.BASE: {"commission": 0.0003, "slippage": 0.0003},
    CostScenario.STRESS: {"commission": 0.0010, "slippage": 0.0010},
}
