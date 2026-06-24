"""Order manager — validates a pending order against the NEXT bar before fill.

A signal is produced on the close of bar T. The order is queued and can only be
filled on bar T+1. This module inspects bar T+1's OPEN (never its high/low — that
would be look-ahead) to decide whether the entry is still valid, and at what
reference price. It returns a rejection reason if the setup degraded overnight
intrabar.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain import Bar, SignalCandidate
from app.enums import RejectionReason

MAX_GAP_ATR = 0.5  # max favourable gap (in ATR) before we refuse to chase
MIN_RR_AFTER_GAP = 1.0  # minimum reward/risk after re-pricing at the open
MAX_SPREAD_BPS = 25.0
MIN_BAR_DOLLAR_VOLUME = 100_000.0


@dataclass
class EntryValidation:
    ok: bool
    entry_price: float = 0.0
    reason: RejectionReason | None = None
    detail: str = ""


def validate_entry(
    candidate: SignalCandidate,
    next_bar: Bar,
    *,
    atr: float,
    spread_bps: float | None = None,
) -> EntryValidation:
    """Validate a long entry at the open of ``next_bar`` (the T+1 bar)."""
    entry = next_bar.open

    # Open already below/at the stop -> the setup is invalid, do not enter.
    if entry <= candidate.stop_price:
        return EntryValidation(False, reason=RejectionReason.OPEN_BELOW_STOP,
                               detail=f"open {entry:.4f} <= stop {candidate.stop_price:.4f}")

    # Liquidity floor.
    if next_bar.dollar_volume < MIN_BAR_DOLLAR_VOLUME:
        return EntryValidation(False, reason=RejectionReason.LIQUIDITY_TOO_LOW,
                               detail=f"bar $vol {next_bar.dollar_volume:.0f}")

    # Spread too wide.
    if spread_bps is not None and spread_bps > MAX_SPREAD_BPS:
        return EntryValidation(False, reason=RejectionReason.SPREAD_TOO_WIDE,
                               detail=f"spread {spread_bps:.1f}bps")

    # Gap-up too large -> we would be chasing far above the planned entry.
    if atr > 0:
        gap_atr = (entry - candidate.reference_price) / atr
        if gap_atr > MAX_GAP_ATR:
            return EntryValidation(False, reason=RejectionReason.GAP_TOO_LARGE,
                                   detail=f"gap {gap_atr:.2f} ATR above reference")

    # Re-price reward/risk at the actual open.
    new_risk = entry - candidate.stop_price
    new_reward = candidate.target_price - entry
    if new_risk <= 0 or (new_reward / new_risk) < MIN_RR_AFTER_GAP:
        rr = (new_reward / new_risk) if new_risk > 0 else 0.0
        return EntryValidation(False, reason=RejectionReason.RISK_REWARD_DEGRADED,
                               detail=f"rr {rr:.2f} after gap")

    return EntryValidation(True, entry_price=entry)
