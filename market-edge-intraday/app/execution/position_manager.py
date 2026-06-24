"""Position manager — decides per-bar exits for open positions.

On each completed bar it checks, for every open position:
  1. stop loss hit (bar low <= stop)
  2. take profit hit (bar high >= target)
  3. time stop (held >= max bars)
  4. trailing / break-even stop adjustments (move stop up; never down)

Ambiguous-bar policy: if a single bar touches BOTH the stop and the target we
cannot know the intrabar path, so we assume the STOP filled first (conservative).
End-of-day flattening is driven by the engine, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain import Bar, PaperPosition
from app.enums import ExitReason, Side


@dataclass
class ExitDecision:
    should_exit: bool
    exit_reason: ExitReason | None = None
    exit_price: float = 0.0


def update_trailing_stop(pos: PaperPosition, atr: float) -> None:
    """Move the stop up for winners (break-even at 1R, trail after the trail-R).

    Stops only ever ratchet UP for a long — never loosen.
    """
    if pos.side != Side.LONG or pos.initial_risk_per_share <= 0:
        return
    r = pos.r_multiple
    be_at = float(pos.metadata.get("breakeven_at_r", 0.0) or 0.0)
    trail_after = float(pos.metadata.get("trail_after_r", 0.0) or 0.0)
    trail_atr = float(pos.metadata.get("trail_atr", 0.0) or 0.0)

    # Break-even (plus a tick to cover costs) once we reach be_at R.
    if be_at and r >= be_at and not pos.moved_to_breakeven:
        be_stop = pos.entry_price * 1.001
        if be_stop > pos.stop_price:
            pos.stop_price = be_stop
            pos.moved_to_breakeven = True

    # Trailing stop once we reach trail_after R.
    if trail_after and trail_atr and atr > 0 and r >= trail_after:
        trailed = pos.highest_close - trail_atr * atr
        if trailed > pos.stop_price:
            pos.stop_price = trailed


def evaluate_exit(
    pos: PaperPosition,
    bar: Bar,
    *,
    atr: float = 0.0,
    max_holding_bars: int | None = None,
) -> ExitDecision:
    """Return an exit decision for ``pos`` given the just-closed ``bar``."""
    # Adjust trailing/break-even using the latest close BEFORE testing the stop.
    update_trailing_stop(pos, atr)

    limit = max_holding_bars
    if limit is None:
        limit = int(pos.metadata.get("max_holding_bars", 8))

    if pos.side == Side.LONG:
        hit_stop = bar.low <= pos.stop_price
        hit_target = bar.high >= pos.target_price
        if hit_stop and hit_target:
            # Conservative: assume stop first.
            return ExitDecision(True, ExitReason.STOP_LOSS, pos.stop_price)
        if hit_stop:
            return ExitDecision(True, ExitReason.STOP_LOSS, pos.stop_price)
        if hit_target:
            return ExitDecision(True, ExitReason.TAKE_PROFIT, pos.target_price)
    else:  # SHORT (reserved; symmetric)
        hit_stop = bar.high >= pos.stop_price
        hit_target = bar.low <= pos.target_price
        if hit_stop and hit_target:
            return ExitDecision(True, ExitReason.STOP_LOSS, pos.stop_price)
        if hit_stop:
            return ExitDecision(True, ExitReason.STOP_LOSS, pos.stop_price)
        if hit_target:
            return ExitDecision(True, ExitReason.TAKE_PROFIT, pos.target_price)

    # Time stop (evaluated on the close of the bar).
    if pos.bars_held >= limit:
        return ExitDecision(True, ExitReason.TIME_STOP, bar.close)

    return ExitDecision(False)
