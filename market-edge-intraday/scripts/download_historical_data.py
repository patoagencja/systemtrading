"""Download historical intraday bars and cache them to parquet.

Usage:
    python scripts/download_historical_data.py --symbols AAPL,MSFT \\
        --start 2026-01-01 --end 2026-03-31 --timeframe 15Min
    python scripts/download_historical_data.py --universe \\
        --start 2026-01-01 --end 2026-03-31
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import sys
from datetime import date, datetime


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch historical bars from the provider and store them as parquet.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--symbols", help="Comma-separated tickers, e.g. AAPL,MSFT,NVDA.")
    src.add_argument(
        "--universe",
        action="store_true",
        help="Use today's built universe instead of an explicit symbol list.",
    )
    parser.add_argument("--start", type=_parse_date, required=True, help="Start date YYYY-MM-DD.")
    parser.add_argument("--end", type=_parse_date, required=True, help="End date YYYY-MM-DD.")
    parser.add_argument("--timeframe", default="15Min", help="Bar size. Default: 15Min.")
    parser.add_argument("--log-level", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.data import get_provider
    from app.data.bar_cache import save_bars
    from app.logging_config import configure_logging, get_logger

    configure_logging(args.log_level)
    logger = get_logger("scripts.download_historical_data")

    try:
        provider = get_provider()
    except RuntimeError as exc:
        print(f"ERROR: market-data provider unavailable: {exc}", file=sys.stderr)
        print(
            "\nNo API key configured. Add ALPACA_API_KEY and ALPACA_SECRET_KEY to your .env\n"
            "(free key from https://alpaca.markets -> Paper Trading -> API Keys).\n",
            file=sys.stderr,
        )
        return 2

    symbols = _resolve_symbols(args, provider, logger)
    if not symbols:
        print("ERROR: no symbols resolved to download.", file=sys.stderr)
        return 2

    logger.info(
        "Downloading %s bars for %d symbols (%s -> %s)",
        args.timeframe,
        len(symbols),
        args.start,
        args.end,
    )
    try:
        bars = provider.get_historical_bars(symbols, args.start, args.end, args.timeframe)
    except RuntimeError as exc:
        print(f"ERROR: download failed: {exc}", file=sys.stderr)
        return 2

    n_saved = _save(bars, args.timeframe, save_bars, logger)
    print(f"OK: downloaded and cached {n_saved} symbol datasets ({args.timeframe}).")
    return 0


def _resolve_symbols(args: argparse.Namespace, provider: object, logger) -> list[str]:
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    from datetime import date as _date

    from app.universe.builder import build_universe

    logger.info("No symbols given; building universe for %s", _date.today())
    snapshot = build_universe(provider, _date.today())
    return _symbols_from_snapshot(snapshot)


def _symbols_from_snapshot(snapshot: object) -> list[str]:
    try:
        import pandas as pd

        if isinstance(snapshot, pd.DataFrame):
            return [str(s).upper() for s in snapshot.get("symbol", [])]
    except Exception:  # pragma: no cover
        pass
    seq = snapshot if isinstance(snapshot, (list, tuple)) else getattr(snapshot, "rows", [])
    out: list[str] = []
    for item in seq or []:
        sym = item.get("symbol") if isinstance(item, dict) else getattr(item, "symbol", None)
        if sym:
            out.append(str(sym).upper())
    return out


def _save(bars: object, timeframe: str, save_bars, logger) -> int:
    """Persist whatever shape the provider returned (dict[symbol]->df or one df)."""
    import pandas as pd

    if isinstance(bars, dict):
        count = 0
        for symbol, frame in bars.items():
            if frame is None or (hasattr(frame, "empty") and frame.empty):
                logger.warning("No data returned for %s", symbol)
                continue
            save_bars(symbol, frame, timeframe)
            count += 1
        return count

    if isinstance(bars, pd.DataFrame) and not bars.empty:
        if "symbol" in bars.columns:
            count = 0
            for symbol, frame in bars.groupby("symbol"):
                save_bars(str(symbol), frame, timeframe)
                count += 1
            return count
        save_bars("ALL", bars, timeframe)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
