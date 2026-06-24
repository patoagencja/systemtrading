"""Event-driven simulator — the shared core of backtest AND live paper trading.

The per-bar trading logic lives in ONE place: :func:`process_bar`. The historical
backtester calls it in a tight loop over a session's bars; the live engine calls
the very same function once per 15-minute tick. There is no second copy of the
trading logic, so backtest and live cannot drift apart.

Strict ordering eliminates look-ahead bias. For each completed bar T:
  1. FILL orders queued on bar T-1, at bar T's OPEN  (entry on T+1, never on T).
  2. MANAGE open positions against bar T's OHLC (stop/target/time stop), mark-to-market.
  3. Force-close everything once the EOD flatten window starts.
  4. GENERATE new signals from bar T's CLOSE and queue them for bar T+1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings
from app.domain import Bar, PendingOrder, SignalCandidate
from app.enums import ExitReason, OrderType, RejectionReason, RunMode, SignalStatus
from app.execution.order_manager import validate_entry
from app.execution.paper_broker import PaperBroker
from app.execution.position_manager import evaluate_exit
from app.indicators.intraday_indicators import compute_indicators
from app.risk.kill_switch import KillSwitch
from app.risk.portfolio_risk import RiskManager
from app.risk.position_sizing import compute_position_size
from app.strategies.base import StrategyContext
from app.strategies.strategy_registry import generate_all

ET = ZoneInfo("America/New_York")


@dataclass
class SignalRecord:
    session_date: date
    signal_time: datetime
    symbol: str
    strategy: str
    score: float
    side: str
    planned_entry: float
    stop_price: float
    target_price: float
    risk_reward: float
    status: str
    rejection_reason: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class BarResult:
    """What happened on a single bar (used for live persistence + accounting)."""

    signals: list[SignalRecord] = field(default_factory=list)
    opened: list = field(default_factory=list)  # PaperPosition
    closed: list = field(default_factory=list)  # ClosedTrade
    pending: list[PendingOrder] = field(default_factory=list)


@dataclass
class SessionResult:
    session_date: date
    signals: list[SignalRecord] = field(default_factory=list)
    trades_opened: int = 0
    trades_closed: int = 0
    scanned_symbols: int = 0
    errors: int = 0
    warnings: int = 0
    daily_pnl_pln: float = 0.0
    end_equity_pln: float = 0.0
    kill_switch_reason: str | None = None


def _row_to_bar(symbol: str, ts: pd.Timestamp, row: pd.Series) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
    )


def _slice_upto(df: pd.DataFrame | None, ts) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    return df.loc[:ts]


def _best_candidate(cands: list[SignalCandidate], min_score: float) -> SignalCandidate | None:
    qualified = [c for c in cands if c.score >= min_score]
    return max(qualified, key=lambda c: c.score) if qualified else None


def process_bar(
    *,
    ts: pd.Timestamp,
    session_bar_index: int,
    bars_remaining: int,
    is_last_bar: bool,
    session_date: date,
    broker: PaperBroker,
    strategies: list,
    risk_manager: RiskManager,
    kill_switch: KillSwitch,
    incoming_pending: list[PendingOrder],
    day_bars: dict[str, pd.DataFrame],
    benchmark_bars: pd.DataFrame | None,
    sector_bars: dict[str, pd.DataFrame] | None,
    prev_closes: dict[str, float],
    universe_symbols: set[str],
    sector_of: dict[str, str],
    sector_etf_of: dict[str, str],
    min_score: float,
) -> BarResult:
    """Process exactly one completed bar. Mutates ``broker``. Returns events.

    ``day_bars`` holds, per symbol, all session bars up to AND INCLUDING ``ts``.
    ``incoming_pending`` are orders queued on the previous bar (filled here).
    """
    out = BarResult()
    et_time: time = ts.tz_convert(ET).time() if getattr(ts, "tzinfo", None) else ts.time()
    sector_bars = sector_bars or {}
    last_entry = settings.last_new_entry_time
    force_close = settings.force_close_start

    # ---- 1) Fill orders queued on the previous bar at THIS bar's open ----
    for order in incoming_pending:
        sym = order.candidate.symbol
        df = day_bars.get(sym)
        if df is None or ts not in df.index:
            out.signals.append(_rejected(session_date, order.candidate,
                                          RejectionReason.STALE_DATA, "no T+1 bar"))
            continue
        next_bar = _row_to_bar(sym, ts, df.loc[ts])
        atr = float(order.candidate.metadata.get("atr", 0.0))
        ev = validate_entry(order.candidate, next_bar, atr=atr,
                            spread_bps=order.candidate.metadata.get("spread_bps"))
        if not ev.ok:
            out.signals.append(_rejected(session_date, order.candidate, ev.reason, ev.detail))
            continue
        sector = sector_of.get(sym, "UNKNOWN")
        state = broker.build_risk_state(universe_symbols)
        decision = risk_manager.check_new_entry(
            symbol=sym, strategy=order.candidate.strategy, sector=sector, state=state)
        if not decision.allowed:
            out.signals.append(_rejected(session_date, order.candidate,
                                          decision.reason, decision.detail))
            continue
        sizing = compute_position_size(
            entry_price_usd=ev.entry_price,
            stop_price_usd=order.candidate.stop_price,
            equity_pln=state.equity_pln,
            cash_pln=state.cash_pln,
            usdpln=broker.usdpln,
            bar_dollar_volume_usd=next_bar.dollar_volume,
            adv_usd=order.candidate.metadata.get("adv_usd"),
            gross_exposure_room_pln=decision.gross_exposure_room_pln,
        )
        if not sizing.ok:
            out.signals.append(_rejected(session_date, order.candidate,
                                          RejectionReason.ZERO_OR_NEGATIVE_SIZE,
                                          sizing.binding_constraint))
            continue
        pos = broker.open_position(
            order.candidate, sizing.shares, ev.entry_price, next_bar,
            ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            sector=sector, spread_bps=order.candidate.metadata.get("spread_bps"))
        out.opened.append(pos)
        out.signals.append(_signal(session_date, order.candidate, SignalStatus.FILLED,
                                    planned_entry=ev.entry_price))

    # ---- 2) Manage open positions against THIS bar ----
    prices: dict[str, float] = {}
    for sym in list(broker.positions.keys()):
        pos = broker.positions[sym]
        df = day_bars.get(sym)
        if df is None or ts not in df.index:
            continue
        bar = _row_to_bar(sym, ts, df.loc[ts])
        pos.bars_held += 1
        atr = float(pos.metadata.get("atr", 0.0))
        decision = evaluate_exit(pos, bar, atr=atr)
        if decision.should_exit:
            trade = broker.close_position(
                sym, decision.exit_price, bar,
                ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                decision.exit_reason, holding_bars=pos.bars_held)
            if trade:
                out.closed.append(trade)
        else:
            prices[sym] = bar.close
    broker.mark_to_market(prices)

    # ---- 3) EOD flatten ----
    if (et_time >= force_close or is_last_bar) and broker.positions:
        _flatten_all(broker, day_bars, ts, out)

    # Kill-switch anomaly checks.
    kill_switch.check_consecutive_losses(broker.consecutive_losses)
    kill_switch.check_daily_loss(broker.daily_realized_pnl_pln, max(1.0, broker.equity_pln()))
    if kill_switch.active and broker.positions:
        _flatten_all(broker, day_bars, ts, out, reason=ExitReason.KILL_SWITCH)

    # ---- 4) Generate new signals from THIS bar's close (queue for T+1) ----
    can_enter = (
        not is_last_bar
        and et_time < last_entry
        and et_time < force_close
        and not kill_switch.active
        and broker.daily_realized_pnl_pln
        > -settings.max_daily_loss_pct * max(1.0, broker.equity_pln())
    )
    if can_enter:
        bench_slice = _slice_upto(benchmark_bars, ts)
        for sym, df in day_bars.items():
            if sym not in universe_symbols or ts not in df.index:
                continue
            sym_slice = df.loc[:ts]
            if sym_slice.empty:
                continue
            sym_slice.attrs["symbol"] = sym
            etf = sector_etf_of.get(sym)
            sec_slice = _slice_upto(sector_bars.get(etf) if etf else None, ts)
            snap = compute_indicators(
                sym_slice, benchmark_bars=bench_slice, sector_bars=sec_slice,
                prev_session_close=prev_closes.get(sym))
            ctx = StrategyContext(
                now_utc=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                now_et=et_time, session_bar_index=session_bar_index,
                bars_remaining=bars_remaining, spy_trend=snap.spy_trend)
            cands = generate_all(strategies, snap, ctx)
            if not cands:
                continue
            best = _best_candidate(cands, min_score)
            for c in cands:
                chosen = best is not None and c is best
                status = SignalStatus.PENDING if chosen else SignalStatus.EXPIRED
                out.signals.append(_signal(session_date, c, status, chosen=chosen,
                                           all_strategies=[x.strategy.value for x in cands]))
            if best is not None:
                if best.symbol in broker.positions or any(
                    o.candidate.symbol == best.symbol for o in out.pending
                ):
                    continue
                best.metadata["atr"] = snap.atr14
                out.pending.append(PendingOrder(
                    candidate=best, quantity=0, order_type=OrderType.MARKET,
                    created_at=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts))
    return out


def simulate_session(
    *,
    session_date: date,
    broker: PaperBroker,
    strategies: list,
    risk_manager: RiskManager,
    kill_switch: KillSwitch,
    day_bars: dict[str, pd.DataFrame],
    benchmark_bars: pd.DataFrame | None,
    sector_bars: dict[str, pd.DataFrame] | None,
    prev_closes: dict[str, float] | None,
    universe_symbols: set[str],
    sector_of: dict[str, str],
    sector_etf_of: dict[str, str],
    min_score: float,
    run_mode: RunMode,
    equity_rows: list | None = None,
) -> SessionResult:
    """Backtest one full trading session by calling ``process_bar`` per bar."""
    result = SessionResult(session_date=session_date)
    broker.reset_daily()
    start_equity = broker.equity_pln()
    prev_closes = prev_closes or {}

    if benchmark_bars is not None and not benchmark_bars.empty:
        grid = list(benchmark_bars.index)
    else:
        idx: set = set()
        for df in day_bars.values():
            idx.update(df.index)
        grid = sorted(idx)
    if not grid:
        result.end_equity_pln = broker.equity_pln()
        return result

    result.scanned_symbols = len(day_bars)
    pending: list[PendingOrder] = []

    for i, ts in enumerate(grid):
        is_last = i == len(grid) - 1
        # day_bars sliced to <= ts is achieved inside process_bar via .loc[:ts];
        # but the fill step needs the row AT ts, which exists in the full-day frame.
        bar_res = process_bar(
            ts=ts, session_bar_index=i, bars_remaining=len(grid) - i - 1,
            is_last_bar=is_last, session_date=session_date, broker=broker,
            strategies=strategies, risk_manager=risk_manager, kill_switch=kill_switch,
            incoming_pending=pending, day_bars=day_bars, benchmark_bars=benchmark_bars,
            sector_bars=sector_bars, prev_closes=prev_closes,
            universe_symbols=universe_symbols, sector_of=sector_of,
            sector_etf_of=sector_etf_of, min_score=min_score,
        )
        pending = bar_res.pending
        result.signals.extend(bar_res.signals)
        result.trades_opened += len(bar_res.opened)
        result.trades_closed += len(bar_res.closed)
        if equity_rows is not None:
            equity_rows.append({
                "timestamp": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                "session_date": session_date, "run_mode": run_mode.value,
                "equity_pln": broker.equity_pln(), "cash_pln": broker.cash_pln,
                "invested_pln": broker.invested_pln(),
                "open_positions": len(broker.positions), "drawdown": broker.drawdown(),
            })

    if broker.positions:  # safety net: never overnight
        _flatten_all(broker, day_bars, grid[-1], BarResult())

    result.daily_pnl_pln = broker.equity_pln() - start_equity
    result.end_equity_pln = broker.equity_pln()
    if kill_switch.active:
        result.kill_switch_reason = kill_switch.reason
    return result


def _flatten_all(broker, day_bars, ts, out: BarResult, reason=ExitReason.END_OF_DAY_FLATTEN):
    py_ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    for sym in list(broker.positions.keys()):
        pos = broker.positions[sym]
        df = day_bars.get(sym)
        if df is not None and ts in df.index:
            bar = _row_to_bar(sym, ts, df.loc[ts])
            price = bar.close
        else:
            bar = Bar(sym, py_ts, pos.current_price, pos.current_price,
                      pos.current_price, pos.current_price, 0.0)
            price = pos.current_price
        trade = broker.close_position(sym, price, bar, py_ts, reason, holding_bars=pos.bars_held)
        if trade:
            out.closed.append(trade)


def _signal(session_date, cand: SignalCandidate, status, *, planned_entry: float | None = None,
            chosen: bool = True, all_strategies: list | None = None) -> SignalRecord:
    meta = dict(cand.metadata)
    if all_strategies:
        meta["matched_strategies"] = all_strategies
        meta["chosen"] = chosen
    return SignalRecord(
        session_date=session_date, signal_time=cand.signal_time, symbol=cand.symbol,
        strategy=cand.strategy.value, score=cand.score, side=cand.side.value,
        planned_entry=planned_entry if planned_entry is not None else cand.reference_price,
        stop_price=cand.stop_price, target_price=cand.target_price,
        risk_reward=cand.risk_reward, status=status.value, metadata=meta)


def _rejected(session_date, cand: SignalCandidate, reason, detail: str) -> SignalRecord:
    return SignalRecord(
        session_date=session_date, signal_time=cand.signal_time, symbol=cand.symbol,
        strategy=cand.strategy.value, score=cand.score, side=cand.side.value,
        planned_entry=cand.reference_price, stop_price=cand.stop_price,
        target_price=cand.target_price, risk_reward=cand.risk_reward,
        status=SignalStatus.REJECTED.value,
        rejection_reason=reason.value if reason else None,
        metadata={"detail": detail, **cand.metadata})
