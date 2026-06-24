"""Fill model, slippage/costs, entry validation, exits."""
from __future__ import annotations

from datetime import UTC, datetime

from app.domain import Bar, PaperPosition, SignalCandidate
from app.enums import CostScenario, ExitReason, Side, StrategyName
from app.execution.fill_model import FillModel
from app.execution.order_manager import validate_entry
from app.execution.position_manager import evaluate_exit, update_trailing_stop
from app.execution.slippage_model import SlippageModel

TS = datetime(2024, 3, 1, 15, 0, tzinfo=UTC)


def _bar(o=100.0, h=101.0, low=99.0, c=100.5, v=2_000_000.0) -> Bar:
    return Bar("X", TS, o, h, low, c, v)


def test_long_entry_pays_up_exit_receives_less():
    fm = FillModel(CostScenario.BASE)
    bar = _bar()
    entry = fm.build_fill(symbol="X", timestamp=TS, side=Side.LONG, is_entry=True,
                          reference_price=100.0, shares=100, bar=bar)
    exit_ = fm.build_fill(symbol="X", timestamp=TS, side=Side.LONG, is_entry=False,
                          reference_price=100.0, shares=100, bar=bar)
    assert entry.fill_price > 100.0  # buy pays the spread + slippage
    assert exit_.fill_price < 100.0  # sell receives less
    assert entry.commission_usd > 0


def test_cost_scenarios_increase_costs():
    bar = _bar()
    low = FillModel(CostScenario.LOW).build_fill(
        symbol="X", timestamp=TS, side=Side.LONG, is_entry=True,
        reference_price=100.0, shares=100, bar=bar)
    stress = FillModel(CostScenario.STRESS).build_fill(
        symbol="X", timestamp=TS, side=Side.LONG, is_entry=True,
        reference_price=100.0, shares=100, bar=bar)
    assert stress.fill_price > low.fill_price
    assert stress.commission_usd > low.commission_usd


def test_slippage_grows_with_participation():
    sm = SlippageModel(CostScenario.BASE)
    small = sm.slippage_pct(1_000, 10_000_000)
    big = sm.slippage_pct(1_000_000, 10_000_000)
    assert big > small


def test_entry_rejected_when_open_below_stop():
    cand = SignalCandidate("X", StrategyName.OPENING_RANGE_BREAKOUT, Side.LONG, TS,
                           100.0, 99.0, 103.0)
    nb = Bar("X", TS, 98.5, 99.0, 98.0, 98.7, 2_000_000)
    ev = validate_entry(cand, nb, atr=0.5)
    assert not ev.ok and ev.reason.value == "OPEN_BELOW_STOP"


def test_entry_rejected_on_large_gap():
    cand = SignalCandidate("X", StrategyName.OPENING_RANGE_BREAKOUT, Side.LONG, TS,
                           100.0, 99.0, 103.0)
    nb = Bar("X", TS, 102.0, 102.5, 101.5, 102.2, 2_000_000)  # gap up >0.5 ATR
    ev = validate_entry(cand, nb, atr=1.0)
    assert not ev.ok and ev.reason.value == "GAP_TOO_LARGE"


def test_entry_accepted_normal_open():
    cand = SignalCandidate("X", StrategyName.OPENING_RANGE_BREAKOUT, Side.LONG, TS,
                           100.0, 98.0, 104.0)
    nb = Bar("X", TS, 100.1, 100.4, 99.9, 100.3, 2_000_000)
    ev = validate_entry(cand, nb, atr=1.0)
    assert ev.ok and ev.entry_price == 100.1


def _pos(stop=98.0, target=104.0, max_bars=8) -> PaperPosition:
    return PaperPosition(
        symbol="X", strategy=StrategyName.OPENING_RANGE_BREAKOUT, side=Side.LONG,
        quantity=100, entry_price=100.0, raw_entry_price=100.0, stop_price=stop,
        target_price=target, initial_risk_per_share=2.0, opened_at=TS,
        current_price=100.0, highest_close=100.0, metadata={"max_holding_bars": max_bars},
    )


def test_stop_loss_exit():
    pos = _pos()
    d = evaluate_exit(pos, _bar(o=99, h=99.5, low=97.5, c=98.5))
    assert d.should_exit and d.exit_reason == ExitReason.STOP_LOSS and d.exit_price == 98.0


def test_take_profit_exit():
    pos = _pos()
    d = evaluate_exit(pos, _bar(o=103, h=104.5, low=102.5, c=104.2))
    assert d.should_exit and d.exit_reason == ExitReason.TAKE_PROFIT and d.exit_price == 104.0


def test_ambiguous_bar_assumes_stop_first():
    pos = _pos()
    # Bar spans both stop and target -> conservative stop.
    d = evaluate_exit(pos, _bar(o=100, h=105, low=97, c=104))
    assert d.exit_reason == ExitReason.STOP_LOSS


def test_time_stop():
    pos = _pos(max_bars=3)
    pos.bars_held = 3
    d = evaluate_exit(pos, _bar(o=100, h=100.5, low=99.5, c=100.2))
    assert d.should_exit and d.exit_reason == ExitReason.TIME_STOP


def test_trailing_stop_only_ratchets_up():
    pos = _pos()
    pos.metadata.update({"breakeven_at_r": 1.0})
    pos.current_price = 102.5  # +1.25R
    pos.highest_close = 102.5
    update_trailing_stop(pos, atr=1.0)
    assert pos.stop_price > 98.0  # moved to break-even
    assert pos.moved_to_breakeven
