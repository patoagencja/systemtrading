"""Daily tradable-universe builder.

Pipeline (``build_universe``):
    1. ``provider.get_assets()`` -> keep common stock / ETF only.
    2. Fetch ~20 sessions of DAILY bars per candidate.
    3. Compute 20-session avg dollar volume, ADV, daily ATR, last price.
    4. Apply the liquidity filter (price / volume / dollar-vol / history gates).
    5. Rank by avg dollar volume (then volume, completeness, volatility).
    6. Take the top ``target_size`` (default ``settings.universe_target_size``).
    7. Write ``data/universe/YYYY-MM-DD.csv`` and persist to the DB.

Works end-to-end with the FixtureProvider for tests. Live providers raise if
credentials are missing — the universe is never fabricated.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings
from app.data.provider_base import MarketDataProvider
from app.enums import AssetType
from app.logging_config import get_logger
from app.universe import liquidity_filter as lf
from app.universe import universe_repository as repo
from app.universe.sector_mapping import default_sector

logger = get_logger(__name__)

ET = ZoneInfo("America/New_York")
_ELIGIBLE_TYPES = {AssetType.COMMON_STOCK.value, AssetType.ETF.value}
_LOOKBACK_SESSIONS = 20
_UNIVERSE_DIR = Path("data/universe")


@dataclass
class UniverseEntry:
    """One ranked, included symbol in a daily universe snapshot."""

    symbol: str
    name: str | None
    sector: str | None
    industry: str | None
    asset_type: str
    price: float
    adv: float
    dollar_volume: float
    atr_daily: float
    spread: float
    rank: int
    inclusion_reason: str


def _daily_atr(df: pd.DataFrame, window: int = 14) -> float:
    """Wilder-style daily ATR from a daily-bars DataFrame (best-effort)."""
    if len(df) < 2:
        return 0.0
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(window=min(window, len(tr)), min_periods=1).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else 0.0


def _metrics_from_daily(
    symbol: str, df: pd.DataFrame, asset_type: str
) -> lf.LiquidityMetrics | None:
    """Compute liquidity metrics over the most recent sessions."""
    if df is None or df.empty:
        return None
    recent = df.tail(_LOOKBACK_SESSIONS)
    if recent.empty:
        return None
    price = float(recent["close"].iloc[-1])
    adv = float(recent["volume"].mean())
    dollar_volume = float((recent["close"] * recent["volume"]).mean())
    history_days = int(len(df))
    atr = _daily_atr(df)
    return lf.LiquidityMetrics(
        symbol=symbol,
        price=price,
        adv=adv,
        dollar_volume=dollar_volume,
        history_days=history_days,
        asset_type=asset_type,
        atr_daily=atr,
    )


def _write_csv(snapshot_date: date, entries: list[UniverseEntry]) -> Path:
    _UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    path = _UNIVERSE_DIR / f"{snapshot_date.isoformat()}.csv"
    fieldnames = list(asdict(entries[0]).keys()) if entries else [
        "symbol", "name", "sector", "industry", "asset_type", "price", "adv",
        "dollar_volume", "atr_daily", "spread", "rank", "inclusion_reason",
    ]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for e in entries:
            writer.writerow(asdict(e))
    logger.info("Wrote universe CSV: %s (%d rows)", path, len(entries))
    return path


def build_universe(
    provider: MarketDataProvider,
    as_of_date: date,
    target_size: int | None = None,
    persist: bool = True,
) -> list[UniverseEntry]:
    """Build, rank, persist and return the universe for ``as_of_date``.

    ``target_size`` defaults to ``settings.universe_target_size``. Set
    ``persist=False`` to skip DB/CSV writes (useful in unit tests).
    """
    target = target_size or settings.universe_target_size

    assets = provider.get_assets()
    if not assets:
        raise RuntimeError(
            f"Provider {provider.name!r} returned no assets; cannot build a "
            "universe. Check credentials/connectivity — data is never fabricated."
        )

    eligible = {
        a["symbol"]: a
        for a in assets
        if a.get("symbol") and a.get("asset_type") in _ELIGIBLE_TYPES
    }
    symbols = sorted(eligible)
    logger.info("Universe candidates after asset-type filter: %d", len(symbols))

    # Daily bars window: enough calendar days to satisfy the history gate
    # (settings.min_history_days trading sessions) plus a weekend/holiday buffer.
    # ~252 trading days per 365 calendar days => scale up by ~1.5x and pad.
    needed_sessions = max(_LOOKBACK_SESSIONS, settings.min_history_days)
    window_days = int(needed_sessions * 1.5) + 15
    end_dt = datetime.combine(as_of_date, time(0, 0), tzinfo=ET).astimezone(UTC)
    start_dt = end_dt - pd.Timedelta(days=window_days)
    daily = provider.get_historical_bars(symbols, start_dt, end_dt, timeframe="1Day")

    metrics: list[lf.LiquidityMetrics] = []
    for sym in symbols:
        m = _metrics_from_daily(sym, daily.get(sym), eligible[sym]["asset_type"])
        if m is not None:
            metrics.append(m)

    passing = lf.filter_candidates(metrics)
    top = passing[:target]

    entries: list[UniverseEntry] = []
    for rank, verdict in enumerate(top, start=1):
        m = verdict.metrics
        asset = eligible[m.symbol]
        sector = asset.get("sector") or default_sector(m.symbol)
        # Synthetic spread proxy: providers expose true spreads via snapshots;
        # here we use a small price-relative estimate for ranking/record only.
        spread = round(max(0.01, m.price * 0.0002), 4)
        entries.append(
            UniverseEntry(
                symbol=m.symbol,
                name=asset.get("name"),
                sector=sector,
                industry=asset.get("industry"),
                asset_type=m.asset_type,
                price=round(m.price, 4),
                adv=round(m.adv, 2),
                dollar_volume=round(m.dollar_volume, 2),
                atr_daily=round(m.atr_daily, 4),
                spread=spread,
                rank=rank,
                inclusion_reason=(
                    f"rank {rank}/{len(top)} by 20d $vol="
                    f"{m.dollar_volume:,.0f}"
                ),
            )
        )

    logger.info(
        "Built universe for %s: %d included (target %d) from %d candidates.",
        as_of_date, len(entries), target, len(symbols),
    )

    if persist and entries:
        _write_csv(as_of_date, entries)
        repo.save_snapshot(as_of_date, entries)
        repo.upsert_instruments(entries)

    return entries
