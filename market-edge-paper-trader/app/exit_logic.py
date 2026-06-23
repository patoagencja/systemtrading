"""
SIMPLE_DYNAMIC_EXIT_V1 — swing position management.

Two variants tested in backtest:
  FIXED_TP_DYNAMIC_STOP   — close at TP (+10%), dynamic stop
  TRAILING_AFTER_10       — above +10% use trailing stop instead of fixed TP

The same logic runs in both live paper trading and backtest so results are
identical. State (TradeState) is held in memory during a session and persisted
to the DB after each session; on restart it is rebuilt from the DB.

Design invariants:
  * Active stop never moves DOWN (monotonic ratchet).
  * Stop updates computed at the close of session N take effect from session
    N+1 onwards (no look-ahead bias). process_session_bar evaluates exits with
    the stop that was active *coming into* the session, then updates the stop at
    the very end.
  * Gap handling is conservative: if the session opens at/below the active stop
    we exit at the OPEN price (which may be worse than the stop).
"""
from dataclasses import dataclass
import logging

from app.config import (
    MAX_INITIAL_STOP_DISTANCE_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    MAX_TAKE_PROFIT_PCT,
    BREAK_EVEN_TRIGGER_PCT,
    PROFIT_LOCK_TRIGGER_1_PCT,
    PROFIT_LOCK_LEVEL_1_PCT,
    PROFIT_LOCK_TRIGGER_2_PCT,
    PROFIT_LOCK_LEVEL_2_PCT,
    PROFIT_LOCK_TRIGGER_3_PCT,
    PROFIT_LOCK_LEVEL_3_PCT,
    TRAILING_STOP_DISTANCE_PCT,
    DEFAULT_MAX_HOLDING_SESSIONS,
    MAX_WINNER_EXTENSION_SESSIONS,
)

logger = logging.getLogger("exit_logic")

EXIT_LOGIC_VERSION = "SIMPLE_DYNAMIC_EXIT_V1"

# Variant identifiers
VARIANT_FIXED_TP = "FIXED_TP_DYNAMIC_STOP"
VARIANT_TRAILING = "TRAILING_AFTER_10"
VARIANT_TRAILING_ONLY = "TRAILING_ONLY"

# Max holding for TRAILING_ONLY variant (extended from default 10 to 15 sessions)
TRAILING_ONLY_MAX_HOLDING = 15


@dataclass
class TradeState:
    """Per-trade runtime state for dynamic exit management.

    NOT persisted between sessions as the source of truth — it is mirrored to
    the DB and rebuilt from it on restart.
    """
    trade_id: int
    ticker: str
    entry_price: float
    initial_stop: float
    active_stop: float
    take_profit: float
    exit_logic_version: str          # "SIMPLE_DYNAMIC_EXIT_V1"
    variant: str                     # "FIXED_TP_DYNAMIC_STOP" | "TRAILING_AFTER_10"

    highest_high: float              # = entry_price at open
    highest_close: float             # = entry_price at open
    max_profit_pct: float            # = 0.0 at open
    stop_status: str                 # INITIAL | BREAK_EVEN | PROFIT_LOCK_2 | PROFIT_LOCK_4 | PROFIT_LOCK_7 | TRAILING
    locked_profit_pct: float         # = 0.0
    sessions_held: int               # = 0
    active_stop_effective_date: str  # date when current stop became active
    max_unrealized_pnl_pct: float    # = 0.0


@dataclass
class ExitEvent:
    exit_price: float
    exit_reason: str
    pnl_pct: float
    sessions_held: int


def make_trade_state(trade_id, ticker, entry_price, initial_stop, take_profit,
                     variant, effective_date) -> TradeState:
    """Construct a fresh TradeState at trade open."""
    return TradeState(
        trade_id=trade_id,
        ticker=ticker,
        entry_price=float(entry_price),
        initial_stop=float(initial_stop),
        active_stop=float(initial_stop),
        take_profit=float(take_profit),
        exit_logic_version=EXIT_LOGIC_VERSION,
        variant=variant,
        highest_high=float(entry_price),
        highest_close=float(entry_price),
        max_profit_pct=0.0,
        stop_status="INITIAL",
        locked_profit_pct=0.0,
        sessions_held=0,
        active_stop_effective_date=str(effective_date),
        max_unrealized_pnl_pct=0.0,
    )


def validate_signal_for_new_logic(signal, entry_price_actual):
    """Validate a signal at T+1 open before executing the trade.

    Returns ((initial_stop, take_profit), None) on success, or (None, reason)
    on rejection.
    """
    initial_stop = float(signal.stop_loss)
    entry = float(entry_price_actual)

    if entry <= 0:
        return None, "INVALID_ENTRY"
    if initial_stop <= 0:
        return None, "INVALID_STOP"
    if initial_stop >= entry:
        return None, "STOP_ABOVE_ENTRY"

    stop_distance = (entry - initial_stop) / entry
    if stop_distance > MAX_INITIAL_STOP_DISTANCE_PCT:
        return None, "INITIAL_STOP_TOO_WIDE"

    tp = entry * (1 + DEFAULT_TAKE_PROFIT_PCT)
    tp = min(tp, entry * (1 + MAX_TAKE_PROFIT_PCT))

    # Verify minimum R:R >= 1.0
    risk = entry - initial_stop
    reward = tp - entry
    if risk <= 0 or reward / risk < 1.0:
        return None, "INSUFFICIENT_RR"

    return (initial_stop, tp), None


def _log_stop_update(state: TradeState, new_stop: float, status: str, session_date: str):
    """Log a stop update at INFO level with full detail."""
    logger.info(
        "STOP_UPDATE trade_id=%s ticker=%s variant=%s session=%s "
        "prev_stop=%.4f new_stop=%.4f status=%s entry=%.4f "
        "max_profit_pct=%.4f highest_close=%.4f locked_profit_pct=%.4f "
        "effective_from=%s (next session)",
        state.trade_id, state.ticker, state.variant, session_date,
        state.active_stop, new_stop, status, state.entry_price,
        state.max_profit_pct, state.highest_close,
        max(0.0, (new_stop - state.entry_price) / state.entry_price),
        session_date,
    )


def process_session_bar(state: TradeState, open_, high, low, close,
                         session_date, entry_cost_pct, variant,
                         no_fixed_tp: bool = False,
                         max_holding_days: int = None) -> "ExitEvent | None":
    """Process one session bar for one open trade.

    Returns an ExitEvent if the trade should be closed this session, else None.
    Mutates `state` with the end-of-session tracking/stop update when no exit
    occurs.

    Args:
        no_fixed_tp: When True, skip the fixed TP check (used by TRAILING_ONLY).
                     Instead the trade can only exit via stop, gap, or max holding.
        max_holding_days: Override for maximum holding sessions. Defaults to
                          DEFAULT_MAX_HOLDING_SESSIONS (10) for most variants,
                          or TRAILING_ONLY_MAX_HOLDING (15) for TRAILING_ONLY.
    """
    open_ = float(open_)
    high = float(high)
    low = float(low)
    close = float(close)
    entry = state.entry_price

    # Determine effective no_fixed_tp flag from variant
    _no_fixed_tp = no_fixed_tp or (variant == VARIANT_TRAILING_ONLY)

    def _pnl_pct(px):
        return (px - entry) / entry

    # ── Step 1: Gap check (BEFORE intra-session) ───────────────────────────
    if open_ <= state.active_stop:
        return ExitEvent(
            exit_price=open_,
            exit_reason="GAP_BELOW_ACTIVE_STOP",
            pnl_pct=_pnl_pct(open_),
            sessions_held=state.sessions_held,
        )
    if not _no_fixed_tp and open_ >= state.take_profit:
        # For both FIXED_TP and TRAILING_AFTER_10: a gap above TP realises at the open.
        return ExitEvent(
            exit_price=open_,
            exit_reason="GAP_ABOVE_TAKE_PROFIT",
            pnl_pct=_pnl_pct(open_),
            sessions_held=state.sessions_held,
        )

    # ── Step 2: Intra-session hits ─────────────────────────────────────────
    sl_hit = low <= state.active_stop
    tp_hit = (not _no_fixed_tp) and (high >= state.take_profit)

    if sl_hit and tp_hit:
        # conservative: stop wins
        return ExitEvent(
            exit_price=state.active_stop,
            exit_reason="stop_loss",
            pnl_pct=_pnl_pct(state.active_stop),
            sessions_held=state.sessions_held,
        )
    elif sl_hit:
        return ExitEvent(
            exit_price=state.active_stop,
            exit_reason="stop_loss",
            pnl_pct=_pnl_pct(state.active_stop),
            sessions_held=state.sessions_held,
        )
    elif tp_hit:
        if variant == VARIANT_FIXED_TP:
            return ExitEvent(
                exit_price=state.take_profit,
                exit_reason="take_profit",
                pnl_pct=_pnl_pct(state.take_profit),
                sessions_held=state.sessions_held,
            )
        # TRAILING_AFTER_10: at +10% don't close — fall through to EOS update
        # which will lock +7% / activate trailing.

    # ── Step 3: Time exit ──────────────────────────────────────────────────
    if max_holding_days is not None:
        max_allowed = max_holding_days
    elif variant == VARIANT_TRAILING_ONLY:
        max_allowed = TRAILING_ONLY_MAX_HOLDING
    else:
        max_allowed = DEFAULT_MAX_HOLDING_SESSIONS
    if (variant == VARIANT_TRAILING and state.max_profit_pct >= 0.10
            and state.stop_status == "TRAILING"):
        max_allowed = DEFAULT_MAX_HOLDING_SESSIONS + MAX_WINNER_EXTENSION_SESSIONS
    if state.sessions_held >= max_allowed:
        return ExitEvent(
            exit_price=close,
            exit_reason="MAX_HOLDING_TIME",
            pnl_pct=_pnl_pct(close),
            sessions_held=state.sessions_held,
        )

    # ── Step 4: End-of-session state update (no exit) ──────────────────────
    state.highest_high = max(state.highest_high, high)
    state.highest_close = max(state.highest_close, close)
    state.max_profit_pct = (state.highest_high - entry) / entry
    state.max_unrealized_pnl_pct = max(
        state.max_unrealized_pnl_pct, (high - entry) / entry
    )
    state.sessions_held += 1

    # Break-even includes round-trip costs (entry + exit).
    be_price = entry * (1 + 2 * entry_cost_pct)

    new_stop_candidate = state.active_stop  # never goes lower
    status = state.stop_status

    if variant == VARIANT_TRAILING_ONLY:
        # TRAILING_ONLY: trailing starts at +8% (not +10%), no fixed TP.
        # From +8%: trailing stop 3% below highest_close_since_entry.
        if state.max_profit_pct >= PROFIT_LOCK_TRIGGER_2_PCT:      # +8% — activate trailing
            trailing = state.highest_close * (1 - TRAILING_STOP_DISTANCE_PCT)
            lock4 = entry * (1 + PROFIT_LOCK_LEVEL_2_PCT)
            new_stop_candidate = max(state.active_stop, lock4, trailing)
            status = "TRAILING"
        elif state.max_profit_pct >= PROFIT_LOCK_TRIGGER_1_PCT:    # +6%: lock +2%
            lock2 = entry * (1 + PROFIT_LOCK_LEVEL_1_PCT)
            new_stop_candidate = max(state.active_stop, lock2)
            status = "PROFIT_LOCK_2"
        elif state.max_profit_pct >= BREAK_EVEN_TRIGGER_PCT:       # +4%: break-even
            new_stop_candidate = max(state.active_stop, be_price)
            status = "BREAK_EVEN"
    else:
        if state.max_profit_pct >= PROFIT_LOCK_TRIGGER_3_PCT:        # +10%
            trailing = state.highest_close * (1 - TRAILING_STOP_DISTANCE_PCT)
            lock7 = entry * (1 + PROFIT_LOCK_LEVEL_3_PCT)
            new_stop_candidate = max(state.active_stop, lock7, trailing)
            status = "TRAILING"
        elif state.max_profit_pct >= PROFIT_LOCK_TRIGGER_2_PCT:      # +8%
            lock4 = entry * (1 + PROFIT_LOCK_LEVEL_2_PCT)
            new_stop_candidate = max(state.active_stop, lock4)
            status = "PROFIT_LOCK_4"
        elif state.max_profit_pct >= PROFIT_LOCK_TRIGGER_1_PCT:      # +6%
            lock2 = entry * (1 + PROFIT_LOCK_LEVEL_1_PCT)
            new_stop_candidate = max(state.active_stop, lock2)
            status = "PROFIT_LOCK_2"
        elif state.max_profit_pct >= BREAK_EVEN_TRIGGER_PCT:         # +4%
            new_stop_candidate = max(state.active_stop, be_price)
            status = "BREAK_EVEN"

    # Enforce: never move stop down.
    new_stop = max(state.active_stop, new_stop_candidate)

    if new_stop > state.active_stop:
        _log_stop_update(state, new_stop, status, session_date)
        state.active_stop_effective_date = str(session_date)

    state.active_stop = new_stop
    state.stop_status = status
    state.locked_profit_pct = max(0.0, (new_stop - entry) / entry)

    return None  # position still open
