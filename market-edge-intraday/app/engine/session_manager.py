"""Session lifecycle + database persistence.

Bridges the in-memory engine state (broker, signals, trades) to the PostgreSQL
tables that the API and dashboard read. Backtest and live use the same tables,
distinguished only by ``run_mode`` so the two streams never mix.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.database import session_scope
from app.domain import ClosedTrade, PaperPosition
from app.enums import RunMode, SessionStatus, Severity, TradeStatus
from app.logging_config import get_logger
from app.models import (
    Heartbeat,
    PortfolioSnapshot,
    Position,
    Signal,
    SystemEvent,
    Trade,
    TradingSession,
)

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_session(run_mode: RunMode, session_date: date) -> int:
    with session_scope() as s:
        row = TradingSession(
            run_mode=run_mode.value,
            session_date=session_date,
            status=SessionStatus.RUNNING.value,
            started_at=_utcnow(),
        )
        s.add(row)
        s.flush()
        return row.id


def finalize_session(
    session_id: int,
    *,
    status: SessionStatus,
    daily_pnl: float,
    end_equity: float,
    counts: dict | None = None,
    kill_switch_reason: str | None = None,
) -> None:
    counts = counts or {}
    with session_scope() as s:
        row = s.get(TradingSession, session_id)
        if row is None:
            return
        row.status = status.value
        row.completed_at = _utcnow()
        row.daily_pnl = daily_pnl
        row.end_equity = end_equity
        row.scanned_symbols = counts.get("scanned_symbols", row.scanned_symbols)
        row.signals_generated = counts.get("signals_generated", row.signals_generated)
        row.trades_opened = counts.get("trades_opened", row.trades_opened)
        row.trades_closed = counts.get("trades_closed", row.trades_closed)
        row.errors = counts.get("errors", row.errors)
        row.warnings = counts.get("warnings", row.warnings)
        row.kill_switch_reason = kill_switch_reason


def save_signals(records, run_mode: RunMode) -> None:
    if not records:
        return
    with session_scope() as s:
        for r in records:
            s.add(Signal(
                run_mode=run_mode.value,
                session_date=r.session_date,
                signal_time=r.signal_time,
                symbol=r.symbol,
                strategy=r.strategy,
                score=r.score,
                side=r.side,
                planned_entry=r.planned_entry,
                stop_price=r.stop_price,
                target_price=r.target_price,
                risk_reward=r.risk_reward,
                status=r.status,
                rejection_reason=r.rejection_reason,
                metadata_json=r.metadata,
                created_at=_utcnow(),
            ))


def save_trade(trade: ClosedTrade, run_mode: RunMode, session_date: date) -> None:
    with session_scope() as s:
        s.add(Trade(
            run_mode=run_mode.value,
            session_date=session_date,
            symbol=trade.symbol,
            strategy=str(trade.strategy),
            side=trade.side.value,
            entry_time=trade.entry_time,
            entry_price=trade.entry_price,
            exit_time=trade.exit_time,
            exit_price=trade.exit_price,
            shares=trade.shares,
            stop_price=trade.stop_price,
            target_price=trade.target_price,
            gross_pnl_usd=trade.gross_pnl_usd,
            net_pnl_usd=trade.net_pnl_usd,
            net_pnl_pln=trade.net_pnl_pln,
            return_pct=trade.return_pct,
            r_multiple=trade.r_multiple,
            holding_bars=trade.holding_bars,
            exit_reason=str(trade.exit_reason),
            costs=trade.costs_usd,
            slippage=trade.slippage_usd,
            usdpln_entry=trade.usdpln_entry,
            usdpln_exit=trade.usdpln_exit,
            fx_pnl_pln=trade.fx_pnl_pln,
            status=TradeStatus.CLOSED.value,
        ))


def sync_positions(positions: dict[str, PaperPosition], run_mode: RunMode) -> None:
    """Replace the stored open positions for this run mode with the live set."""
    with session_scope() as s:
        s.query(Position).filter(Position.run_mode == run_mode.value).delete()
        for pos in positions.values():
            s.add(Position(
                run_mode=run_mode.value,
                symbol=pos.symbol,
                strategy=str(pos.strategy),
                quantity=pos.quantity,
                average_entry=pos.entry_price,
                current_price=pos.current_price,
                stop_price=pos.stop_price,
                target_price=pos.target_price,
                unrealized_pnl=pos.unrealized_pnl_usd,
                open_risk=pos.open_risk_usd,
                opened_at=pos.opened_at,
                updated_at=_utcnow(),
            ))


def save_portfolio_snapshot(
    run_mode: RunMode, session_date: date, *, cash_pln: float, invested_pln: float,
    equity_pln: float, daily_pnl: float, total_pnl: float, exposure: float,
    open_risk: float, drawdown: float, open_positions: int,
) -> None:
    with session_scope() as s:
        s.add(PortfolioSnapshot(
            run_mode=run_mode.value, timestamp=_utcnow(), session_date=session_date,
            cash_pln=cash_pln, invested_pln=invested_pln, equity_pln=equity_pln,
            daily_pnl=daily_pnl, total_pnl=total_pnl, exposure=exposure,
            open_risk=open_risk, drawdown=drawdown, open_positions=open_positions,
        ))


def save_heartbeat(service: str, status: str = "OK", metadata: dict | None = None) -> None:
    with session_scope() as s:
        s.add(Heartbeat(service=service, timestamp=_utcnow(), status=status,
                        metadata_json=metadata or {}))


def log_event(severity: Severity, event_type: str, message: str,
              metadata: dict | None = None) -> None:
    with session_scope() as s:
        s.add(SystemEvent(timestamp=_utcnow(), severity=severity.value,
                          event_type=event_type, message=message,
                          metadata_json=metadata or {}))
    logger.log(getattr(__import__("logging"), severity.value, 20), "%s: %s", event_type, message)


def persist_backtest_result(result, run_mode: RunMode = RunMode.BACKTEST) -> None:
    """Persist a finished backtest so the dashboard can display it.

    Clears any previous BACKTEST rows first so the dashboard shows one study.
    """
    with session_scope() as s:
        for model in (Trade, Signal, PortfolioSnapshot):
            s.query(model).filter(model.run_mode == run_mode.value).delete()
    save_signals(result.signals, run_mode)
    for t in result.trades:
        save_trade(t, run_mode, t.entry_time.date())
    # Equity snapshots (sampled to end-of-day to keep the table small).
    eq = result.equity_curve
    if eq is not None and not eq.empty:
        last_per_day = eq.groupby("session_date").tail(1)
        with session_scope() as s:
            for _, row in last_per_day.iterrows():
                s.add(PortfolioSnapshot(
                    run_mode=run_mode.value, timestamp=row["timestamp"],
                    session_date=row["session_date"], cash_pln=row.get("cash_pln", 0.0),
                    invested_pln=row.get("invested_pln", 0.0), equity_pln=row["equity_pln"],
                    daily_pnl=0.0, total_pnl=row["equity_pln"] - result.config.initial_capital_pln,
                    exposure=row.get("invested_pln", 0.0), open_risk=0.0,
                    drawdown=row.get("drawdown", 0.0),
                    open_positions=int(row.get("open_positions", 0)),
                ))
    logger.info("Persisted backtest: %d trades, %d signals", len(result.trades),
                len(result.signals))
