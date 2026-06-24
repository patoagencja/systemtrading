"""Central configuration loaded from environment / .env via pydantic-settings.

Every tunable lives here so backtest and live paper read the *same* numbers.
Import the singleton with ``from app.config import settings``.
"""
from __future__ import annotations

from datetime import time
from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import CostScenario, StrategyName


def _parse_et(value: str) -> time:
    """Parse a 'HH:MM' America/New_York wall-clock string into a time."""
    hh, mm = value.strip().split(":")
    return time(int(hh), int(mm))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Data provider ------------------------------------------------------
    market_data_provider: str = "alpaca"
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_data_feed: str = "iex"
    alpaca_base_url: str = "https://data.alpaca.markets"
    massive_api_key: str = ""
    massive_base_url: str = "https://api.polygon.io"

    # --- Database / stores --------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "intraday"
    postgres_user: str = "intraday"
    postgres_password: str = "intraday"
    database_url: str = ""  # explicit override
    parquet_dir: str = "data/cache"
    duckdb_path: str = "data/cache/intraday.duckdb"

    # --- Capital / FX -------------------------------------------------------
    initial_capital_pln: float = 1_000_000.0
    fallback_usdpln: float = 4.0

    # --- Session timing (America/New_York) ----------------------------------
    last_new_entry_time_et: str = "15:15"
    force_close_start_et: str = "15:45"
    force_close_deadline_et: str = "15:50"
    bar_interval: str = "15Min"
    max_data_staleness_seconds: int = 1200
    benchmark_symbol: str = "SPY"

    # Regular session bounds (fixed by US equity rules).
    session_open_et: str = "09:30"
    session_close_et: str = "16:00"
    opening_range_end_et: str = "10:00"

    # --- Universe -----------------------------------------------------------
    universe_target_size: int = 500
    min_price_usd: float = 5.0
    max_price_usd: float = 1000.0
    min_avg_daily_volume: float = 1_000_000.0
    min_avg_dollar_volume_usd: float = 25_000_000.0
    min_history_days: int = 100

    # --- Scoring ------------------------------------------------------------
    min_signal_score: float = 75.0

    # --- Risk ---------------------------------------------------------------
    risk_per_trade_pct: float = 0.001
    max_position_value_pln: float = 30_000.0
    max_open_positions: int = 20
    max_gross_exposure_pct: float = 0.50
    max_sector_exposure_pct: float = 0.15
    max_positions_per_sector: int = 3
    max_total_open_risk_pct: float = 0.02
    max_daily_loss_pct: float = 0.01
    max_strategy_daily_loss_pct: float = 0.004
    max_consecutive_losses: int = 8

    # --- Execution / costs --------------------------------------------------
    cost_scenario: CostScenario = CostScenario.BASE
    max_position_bar_volume_pct: float = 0.01
    max_adv_participation_pct: float = 0.05  # max share of avg daily volume

    # --- Strategy specifics -------------------------------------------------
    orb_max_stop_pct: float = 0.025
    enabled_strategies: str = (
        "OPENING_RANGE_BREAKOUT,VWAP_MEAN_REVERSION,"
        "RELATIVE_STRENGTH_MOMENTUM,VOLUME_EXPANSION_MOMENTUM"
    )

    # --- Alerts -------------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    slack_webhook_url: str = ""

    # --- Misc ---------------------------------------------------------------
    log_level: str = "INFO"
    tz: str = "UTC"

    # ------------------------------------------------------------------ derived
    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def last_new_entry_time(self) -> time:
        return _parse_et(self.last_new_entry_time_et)

    @property
    def force_close_start(self) -> time:
        return _parse_et(self.force_close_start_et)

    @property
    def force_close_deadline(self) -> time:
        return _parse_et(self.force_close_deadline_et)

    @property
    def session_open(self) -> time:
        return _parse_et(self.session_open_et)

    @property
    def session_close(self) -> time:
        return _parse_et(self.session_close_et)

    @property
    def opening_range_end(self) -> time:
        return _parse_et(self.opening_range_end_et)

    @property
    def enabled_strategy_set(self) -> set[StrategyName]:
        raw = [s.strip() for s in self.enabled_strategies.split(",") if s.strip()]
        if not raw:
            return set(StrategyName)
        out: set[StrategyName] = set()
        for name in raw:
            try:
                out.add(StrategyName(name))
            except ValueError:
                continue
        return out or set(StrategyName)

    @property
    def commission_per_side(self) -> float:
        from app.enums import COST_TABLE

        return COST_TABLE[self.cost_scenario]["commission"]

    @property
    def slippage_per_side(self) -> float:
        from app.enums import COST_TABLE

        return COST_TABLE[self.cost_scenario]["slippage"]

    @property
    def has_live_data_credentials(self) -> bool:
        provider = self.market_data_provider.lower()
        if provider == "alpaca":
            return bool(self.alpaca_api_key and self.alpaca_secret_key)
        if provider == "massive":
            return bool(self.massive_api_key)
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
