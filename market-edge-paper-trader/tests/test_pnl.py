import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.config import PLN_USD_RATE


def calc_pnl(entry_usd, exit_usd, shares, rate=PLN_USD_RATE):
    pnl_usd = (exit_usd - entry_usd) * shares
    pnl_pln = pnl_usd * rate
    pnl_pct = (exit_usd - entry_usd) / entry_usd * 100
    risk_per_share_usd = entry_usd - (entry_usd * 0.95)  # 5% stop
    risk_pln = risk_per_share_usd * shares * rate
    r_multiple = pnl_pln / risk_pln if risk_pln > 0 else 0
    return {"pnl_usd": pnl_usd, "pnl_pln": pnl_pln, "pnl_pct": pnl_pct, "r": r_multiple}


class TestPnlCalculation:
    def test_winning_trade(self):
        res = calc_pnl(entry_usd=100.0, exit_usd=110.0, shares=100)
        assert res["pnl_usd"] == pytest.approx(1000.0)
        assert res["pnl_pln"] == pytest.approx(1000.0 * PLN_USD_RATE)
        assert res["pnl_pct"] == pytest.approx(10.0)

    def test_losing_trade(self):
        res = calc_pnl(entry_usd=100.0, exit_usd=95.0, shares=100)
        assert res["pnl_usd"] == pytest.approx(-500.0)
        assert res["pnl_pln"] == pytest.approx(-500.0 * PLN_USD_RATE)
        assert res["pnl_pct"] == pytest.approx(-5.0)

    def test_breakeven_trade(self):
        res = calc_pnl(entry_usd=100.0, exit_usd=100.0, shares=50)
        assert res["pnl_usd"] == pytest.approx(0.0)
        assert res["pnl_pln"] == pytest.approx(0.0)

    def test_pln_conversion(self):
        res = calc_pnl(entry_usd=50.0, exit_usd=60.0, shares=10, rate=4.0)
        assert res["pnl_pln"] == pytest.approx(400.0)  # 10 USD * 10 shares * 4.0

    def test_r_multiple_positive(self):
        res = calc_pnl(entry_usd=100.0, exit_usd=110.0, shares=100)
        assert res["r"] > 0

    def test_r_multiple_negative(self):
        res = calc_pnl(entry_usd=100.0, exit_usd=95.0, shares=100)
        assert res["r"] < 0

    def test_stop_loss_exit_exact(self):
        entry = 100.0
        stop = 96.0
        shares = 130.0
        pnl_usd = (stop - entry) * shares
        pnl_pln = pnl_usd * PLN_USD_RATE
        assert pnl_pln == pytest.approx(-4.0 * 130.0 * PLN_USD_RATE)
        assert pnl_pln < 0

    def test_take_profit_exit_exact(self):
        entry = 100.0
        tp = 108.0
        shares = 100.0
        pnl_usd = (tp - entry) * shares
        pnl_pln = pnl_usd * PLN_USD_RATE
        assert pnl_pln > 0
        assert pnl_pln == pytest.approx(8.0 * 100.0 * PLN_USD_RATE)
