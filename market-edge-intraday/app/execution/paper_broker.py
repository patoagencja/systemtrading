"""Virtual paper broker.

Holds the portfolio (cash in PLN, open positions, realised trades) and applies
fills through the :class:`FillModel`. It NEVER talks to a real broker and NEVER
sends real orders — all execution is simulated. Trades are denominated in USD;
the portfolio reports in PLN with the USD result and the FX effect kept separate.
"""
from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.domain import Bar, ClosedTrade, PaperPosition, SignalCandidate
from app.enums import CostScenario, ExitReason, RunMode, Side
from app.execution.fill_model import FillModel
from app.logging_config import get_logger
from app.risk.portfolio_risk import PortfolioRiskState

logger = get_logger(__name__)


class PaperBroker:
    def __init__(
        self,
        run_mode: RunMode = RunMode.BACKTEST,
        initial_capital_pln: float | None = None,
        usdpln: float | None = None,
        cost_scenario: CostScenario | None = None,
    ):
        self.run_mode = run_mode
        self.initial_capital_pln = initial_capital_pln or settings.initial_capital_pln
        self.cash_pln = self.initial_capital_pln
        self.usdpln = usdpln or settings.fallback_usdpln
        self.fill_model = FillModel(cost_scenario)

        self.positions: dict[str, PaperPosition] = {}
        self.closed_trades: list[ClosedTrade] = []
        self.realized_pnl_pln = 0.0
        self.daily_realized_pnl_pln = 0.0
        self.strategy_daily_pnl_pln: dict[str, float] = {}
        self.consecutive_losses = 0
        self.peak_equity_pln = self.initial_capital_pln

    # ------------------------------------------------------------------ pricing
    def set_usdpln(self, rate: float) -> None:
        if rate > 0:
            self.usdpln = rate

    def position_value_pln(self, pos: PaperPosition) -> float:
        return pos.quantity * pos.current_price * self.usdpln

    def equity_pln(self) -> float:
        invested = sum(self.position_value_pln(p) for p in self.positions.values())
        return self.cash_pln + invested

    def invested_pln(self) -> float:
        return sum(self.position_value_pln(p) for p in self.positions.values())

    def gross_exposure_pln(self) -> float:
        return self.invested_pln()

    def total_open_risk_pln(self) -> float:
        return sum(p.open_risk_usd for p in self.positions.values()) * self.usdpln

    def drawdown(self) -> float:
        eq = self.equity_pln()
        self.peak_equity_pln = max(self.peak_equity_pln, eq)
        if self.peak_equity_pln <= 0:
            return 0.0
        return (eq - self.peak_equity_pln) / self.peak_equity_pln

    def mark_to_market(self, prices: dict[str, float]) -> None:
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            if px is not None and px > 0:
                pos.current_price = px
                if pos.side == Side.LONG:
                    pos.highest_close = max(pos.highest_close, px)

    # ------------------------------------------------------------------ trading
    def open_position(
        self,
        candidate: SignalCandidate,
        shares: int,
        entry_reference_price: float,
        bar: Bar,
        timestamp: datetime,
        *,
        sector: str = "UNKNOWN",
        signal_id: int | None = None,
        spread_bps: float | None = None,
    ) -> PaperPosition:
        fill = self.fill_model.build_fill(
            symbol=candidate.symbol,
            timestamp=timestamp,
            side=candidate.side,
            is_entry=True,
            reference_price=entry_reference_price,
            shares=shares,
            bar=bar,
            spread_bps=spread_bps,
        )
        cost_pln = (fill.fill_price * shares + fill.commission_usd) * self.usdpln
        self.cash_pln -= cost_pln

        risk_per_share = abs(fill.fill_price - candidate.stop_price)
        pos = PaperPosition(
            symbol=candidate.symbol,
            strategy=candidate.strategy,
            side=candidate.side,
            quantity=shares,
            entry_price=fill.fill_price,
            raw_entry_price=entry_reference_price,
            stop_price=candidate.stop_price,
            target_price=candidate.target_price,
            initial_risk_per_share=risk_per_share,
            opened_at=timestamp,
            current_price=fill.fill_price,
            highest_close=fill.fill_price,
            entry_costs_usd=fill.commission_usd + fill.spread_cost_usd + fill.slippage_usd,
            usdpln_entry=self.usdpln,
            signal_id=signal_id,
            metadata={
                "sector": sector,
                "entry_commission_usd": fill.commission_usd,
                "entry_slippage_usd": fill.slippage_usd,
                "entry_spread_usd": fill.spread_cost_usd,
                **candidate.metadata,
            },
        )
        self.positions[candidate.symbol] = pos
        logger.info(
            "OPEN %s %s %d @ %.4f (ref %.4f) stop %.4f tgt %.4f",
            candidate.strategy, candidate.symbol, shares, fill.fill_price,
            entry_reference_price, candidate.stop_price, candidate.target_price,
        )
        return pos

    def close_position(
        self,
        symbol: str,
        exit_reference_price: float,
        bar: Bar,
        timestamp: datetime,
        exit_reason: ExitReason,
        *,
        holding_bars: int | None = None,
    ) -> ClosedTrade | None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None

        fill = self.fill_model.build_fill(
            symbol=symbol,
            timestamp=timestamp,
            side=pos.side,
            is_entry=False,
            reference_price=exit_reference_price,
            shares=pos.quantity,
            bar=bar,
        )
        proceeds_pln = (fill.fill_price * pos.quantity - fill.commission_usd) * self.usdpln
        self.cash_pln += proceeds_pln

        # P&L decomposition (long).
        gross_pnl_usd = (fill.fill_price - pos.entry_price) * pos.quantity
        entry_comm = pos.metadata.get("entry_commission_usd", 0.0)
        total_commission = entry_comm + fill.commission_usd
        net_pnl_usd = gross_pnl_usd - total_commission

        usdpln_exit = self.usdpln
        market_pnl_pln = net_pnl_usd * usdpln_exit
        fx_pnl_pln = pos.quantity * pos.entry_price * (usdpln_exit - pos.usdpln_entry)
        net_pnl_pln = market_pnl_pln + fx_pnl_pln

        entry_notional = pos.entry_price * pos.quantity
        return_pct = (net_pnl_usd / entry_notional) if entry_notional else 0.0
        r_multiple = (
            (fill.fill_price - pos.entry_price) / pos.initial_risk_per_share
            if pos.initial_risk_per_share > 0
            else 0.0
        )
        slippage_usd = pos.metadata.get("entry_slippage_usd", 0.0) + fill.slippage_usd
        spread_usd = pos.metadata.get("entry_spread_usd", 0.0) + fill.spread_cost_usd
        costs_usd = total_commission + spread_usd

        trade = ClosedTrade(
            symbol=symbol,
            strategy=pos.strategy,
            side=pos.side,
            entry_time=pos.opened_at,
            entry_price=pos.entry_price,
            exit_time=timestamp,
            exit_price=fill.fill_price,
            shares=pos.quantity,
            stop_price=pos.stop_price,
            target_price=pos.target_price,
            gross_pnl_usd=gross_pnl_usd,
            net_pnl_usd=net_pnl_usd,
            net_pnl_pln=net_pnl_pln,
            return_pct=return_pct,
            r_multiple=r_multiple,
            holding_bars=holding_bars if holding_bars is not None else pos.bars_held,
            exit_reason=exit_reason,
            costs_usd=costs_usd,
            slippage_usd=slippage_usd,
            usdpln_entry=pos.usdpln_entry,
            usdpln_exit=usdpln_exit,
            fx_pnl_pln=fx_pnl_pln,
            signal_id=pos.signal_id,
        )
        self.closed_trades.append(trade)
        self.realized_pnl_pln += net_pnl_pln
        self.daily_realized_pnl_pln += net_pnl_pln
        skey = str(pos.strategy)
        self.strategy_daily_pnl_pln[skey] = self.strategy_daily_pnl_pln.get(skey, 0.0) + net_pnl_pln
        self.consecutive_losses = self.consecutive_losses + 1 if net_pnl_pln < 0 else 0

        logger.info(
            "CLOSE %s %s %d @ %.4f reason=%s net_pln=%.0f R=%.2f",
            pos.strategy, symbol, pos.quantity, fill.fill_price,
            exit_reason, net_pnl_pln, r_multiple,
        )
        return trade

    # ------------------------------------------------------------------ state
    def reset_daily(self) -> None:
        self.daily_realized_pnl_pln = 0.0
        self.strategy_daily_pnl_pln = {}

    def build_risk_state(self, universe_symbols: set[str] | None = None) -> PortfolioRiskState:
        sector_exposure: dict[str, float] = {}
        sector_counts: dict[str, int] = {}
        for pos in self.positions.values():
            sec = pos.metadata.get("sector", "UNKNOWN")
            sector_exposure[sec] = sector_exposure.get(sec, 0.0) + self.position_value_pln(pos)
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
        return PortfolioRiskState(
            equity_pln=self.equity_pln(),
            cash_pln=self.cash_pln,
            open_symbols=set(self.positions.keys()),
            n_open=len(self.positions),
            gross_exposure_pln=self.gross_exposure_pln(),
            total_open_risk_pln=self.total_open_risk_pln(),
            sector_exposure_pln=sector_exposure,
            sector_counts=sector_counts,
            daily_realized_pnl_pln=self.daily_realized_pnl_pln,
            strategy_daily_pnl_pln=dict(self.strategy_daily_pnl_pln),
            consecutive_losses=self.consecutive_losses,
            universe_symbols=universe_symbols or set(),
        )
