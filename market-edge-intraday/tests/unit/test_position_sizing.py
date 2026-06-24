"""Position sizing caps."""
from __future__ import annotations

from app.config import settings
from app.risk.position_sizing import compute_position_size


def test_risk_based_size_respects_risk_per_trade():
    # 1,000,000 PLN * 0.001 = 1000 PLN risk; /4 = 250 USD risk; /2 USD per share = 125 sh
    res = compute_position_size(
        entry_price_usd=50.0, stop_price_usd=48.0, equity_pln=1_000_000,
        cash_pln=1_000_000, usdpln=4.0, bar_dollar_volume_usd=1e9, adv_usd=1e10,
    )
    # But 30k PLN position cap = 7500 USD / 50 = 150 sh; risk cap 125 binds.
    assert res.shares == 125
    assert res.binding_constraint == "risk"


def test_position_value_cap_binds():
    res = compute_position_size(
        entry_price_usd=50.0, stop_price_usd=49.9, equity_pln=1_000_000,
        cash_pln=1_000_000, usdpln=4.0, bar_dollar_volume_usd=1e9, adv_usd=1e10,
    )
    # tiny risk/share -> risk cap huge; position value cap (30k PLN) should bind.
    max_value_usd = settings.max_position_value_pln / 4.0
    assert res.shares == int(max_value_usd / 50.0)
    assert res.binding_constraint == "position_value"


def test_bar_volume_cap_binds():
    res = compute_position_size(
        entry_price_usd=50.0, stop_price_usd=48.0, equity_pln=1_000_000,
        cash_pln=1_000_000, usdpln=4.0, bar_dollar_volume_usd=100_000, adv_usd=1e10,
    )
    # 1% of a 100k bar = 1000 USD / 50 = 20 shares.
    assert res.shares == 20
    assert res.binding_constraint == "bar_volume"


def test_zero_size_when_no_risk():
    res = compute_position_size(
        entry_price_usd=50.0, stop_price_usd=50.0, equity_pln=1_000_000,
        cash_pln=1_000_000, usdpln=4.0, bar_dollar_volume_usd=1e9,
    )
    assert not res.ok
    assert res.shares == 0
