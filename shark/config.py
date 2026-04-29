"""
Central configuration — typed, validated, single source of truth.

Why this exists:
    Before, ~40 environment variables were read with `os.getenv(...)` scattered
    across the codebase. Two issues fell out of that:
        1. No place to see all configuration knobs at once.
        2. No validation — a typo like CIRCUIT_BREAKER_PCT=15 (instead of 0.15)
           would silently allow 1500% drawdown before the breaker tripped.

    This module loads every supported env var ONCE, casts it, and validates
    the resulting value against a sane range. `load_settings()` is called from
    `shark/run.py` at startup so a misconfiguration fails fast — before any
    Alpaca call or order placement.

How to add a new setting:
    1. Add a typed attribute to `Settings` with a default.
    2. Read it from os.environ in `_load_from_env()`.
    3. Validate it in `Settings.validate()` if it has a meaningful range.
    4. Use `get_settings()` from your module rather than `os.getenv` — but
       legacy `os.getenv` call sites continue to work unchanged.

This module deliberately does NOT touch any state outside of os.environ. It
is safe to import from any other module without circular-import risk.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when a configuration value is missing, malformed, or out of range."""


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}={raw!r} is not a valid float") from exc


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}={raw!r} is not a valid int") from exc


def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _require_range(
    name: str, value: float, *, min_v: float, max_v: float,
) -> None:
    if not (min_v <= value <= max_v):
        raise ConfigError(
            f"{name}={value} is outside permitted range [{min_v}, {max_v}]"
        )


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """Typed, validated runtime configuration. Immutable after load."""

    # ----- Risk / sizing -----
    max_positions: int
    max_position_pct: float
    max_weekly_trades: int
    min_cash_buffer_pct: float
    circuit_breaker_pct: float
    max_sector_failures: int
    max_sector_concentration: int
    min_momentum_score: float
    risk_per_trade_pct: float
    kelly_fraction: float

    # ----- Stops / exits -----
    hard_stop_pct: float
    atr_stop_multiple: float
    atr_trail_multiple: float
    trail_pct_min: float
    trail_pct_max: float
    time_decay_days: int
    time_decay_min_move_pct: float
    vol_expansion_threshold: float

    # ----- Multi-agent debate -----
    debate_rounds: int            # 0 = single-call (legacy), 1+ = adversarial rounds
    llm_risk_review: bool         # enable LLM-powered risk debate after guardrails
    risk_debate_rounds: int       # rounds per risk perspective (aggressive/conservative/neutral)

    # ----- Trading mode -----
    trading_mode: str                    # "paper" or "live"
    alpaca_base_url: str                 # Alpaca endpoint (paper vs live)

    # ----- Paper-mode overrides (only apply when trading_mode="paper") -----
    paper_bear_override: bool            # allow limited trades in BEAR regimes
    paper_macro_bypass: bool             # bypass CRITICAL/HIGH macro blocks
    paper_bear_max_trades: int           # max new trades/day in BEAR override
    paper_bear_size_mult: float          # position size multiplier (0.5 = half)
    paper_bear_confidence: float         # min confidence threshold
    paper_bear_min_score: int            # pre-market min score in BEAR regimes

    # ----- Regime detection -----
    regime_atr_high_vol_pct: float
    regime_benchmark: str

    # ----- Backtest -----
    backtest_capital: float
    backtest_lookback_days: int
    backtest_risk_pct: float
    backtest_atr_stop_mult: float
    backtest_momentum_min: float
    backtest_rs_min: float
    backtest_symbols: str

    # ----- API credentials (presence-checked, never logged) -----
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_data_feed: str
    anthropic_api_key: str
    claude_model: str
    perplexity_api_key: str

    # ----- Notifications -----
    notify_email: str
    notify_from_email: str
    gmail_oauth_client_id: str
    gmail_oauth_client_secret: str
    gmail_oauth_refresh_token: str
    gmail_app_password: str
    resend_api_key: str
    resend_from_email: str

    @property
    def is_paper(self) -> bool:
        """True when running in paper-trading mode."""
        return self.trading_mode == "paper"

    @property
    def is_live(self) -> bool:
        """True when running in live-trading mode."""
        return self.trading_mode == "live"

    def validate(self) -> None:
        """Raise ConfigError if any field is out of range. Called once at load."""
        # Trading mode
        if self.trading_mode not in ("paper", "live"):
            raise ConfigError(
                f"TRADING_MODE={self.trading_mode!r} must be 'paper' or 'live'"
            )
        # Safety check: warn if live mode but using paper URL
        if self.is_live and "paper-api" in self.alpaca_base_url:
            logger.warning(
                "TRADING_MODE=live but ALPACA_BASE_URL points to paper-api — "
                "trades will execute on paper account, not real money"
            )
        # Safety check: warn if paper mode but using live URL
        if self.is_paper and "paper-api" not in self.alpaca_base_url:
            logger.warning(
                "TRADING_MODE=paper but ALPACA_BASE_URL points to LIVE endpoint — "
                "trades will execute with REAL money!"
            )

        # Paper-mode overrides
        _require_range("PAPER_BEAR_MAX_TRADES", self.paper_bear_max_trades, min_v=0, max_v=5)
        _require_range("PAPER_BEAR_SIZE_MULT", self.paper_bear_size_mult, min_v=0.1, max_v=1.0)
        _require_range("PAPER_BEAR_CONFIDENCE", self.paper_bear_confidence, min_v=0.5, max_v=1.0)
        _require_range("PAPER_BEAR_MIN_SCORE", self.paper_bear_min_score, min_v=1, max_v=10)

        # Risk / sizing — fractions are 0..1, percentages have explicit upper bounds
        _require_range("MAX_POSITIONS", self.max_positions, min_v=1, max_v=20)
        _require_range("MAX_POSITION_PCT", self.max_position_pct, min_v=0.01, max_v=0.50)
        _require_range("MAX_WEEKLY_TRADES", self.max_weekly_trades, min_v=1, max_v=20)
        _require_range("MIN_CASH_BUFFER_PCT", self.min_cash_buffer_pct, min_v=0.0, max_v=0.90)
        _require_range("CIRCUIT_BREAKER_PCT", self.circuit_breaker_pct, min_v=0.01, max_v=0.50)
        _require_range("MAX_SECTOR_FAILURES", self.max_sector_failures, min_v=1, max_v=10)
        _require_range("MAX_SECTOR_CONCENTRATION", self.max_sector_concentration, min_v=1, max_v=10)
        _require_range("MIN_MOMENTUM_SCORE", self.min_momentum_score, min_v=0.0, max_v=100.0)
        _require_range("RISK_PER_TRADE_PCT", self.risk_per_trade_pct, min_v=0.0, max_v=0.10)
        _require_range("KELLY_FRACTION", self.kelly_fraction, min_v=0.0, max_v=1.0)

        # Stops / exits — hard stop is negative
        if self.hard_stop_pct >= 0:
            raise ConfigError(
                f"HARD_STOP_PCT={self.hard_stop_pct} must be negative (e.g. -0.07 for -7%)"
            )
        _require_range("HARD_STOP_PCT", self.hard_stop_pct, min_v=-0.50, max_v=-0.01)
        _require_range("ATR_STOP_MULTIPLE", self.atr_stop_multiple, min_v=0.5, max_v=10.0)
        _require_range("ATR_TRAIL_MULTIPLE", self.atr_trail_multiple, min_v=0.5, max_v=10.0)
        _require_range("TRAIL_PCT_MIN", self.trail_pct_min, min_v=0.5, max_v=50.0)
        _require_range("TRAIL_PCT_MAX", self.trail_pct_max, min_v=1.0, max_v=50.0)
        if self.trail_pct_min >= self.trail_pct_max:
            raise ConfigError(
                f"TRAIL_PCT_MIN ({self.trail_pct_min}) must be < "
                f"TRAIL_PCT_MAX ({self.trail_pct_max})"
            )
        _require_range("TIME_DECAY_DAYS", self.time_decay_days, min_v=1, max_v=120)
        _require_range("TIME_DECAY_MIN_MOVE_PCT", self.time_decay_min_move_pct, min_v=0.0, max_v=50.0)
        _require_range("VOL_EXPANSION_THRESHOLD", self.vol_expansion_threshold, min_v=1.0, max_v=10.0)

        # Multi-agent debate
        _require_range("DEBATE_ROUNDS", self.debate_rounds, min_v=0, max_v=5)
        _require_range("RISK_DEBATE_ROUNDS", self.risk_debate_rounds, min_v=0, max_v=3)

        # Regime
        _require_range("REGIME_ATR_HIGH_VOL_PCT", self.regime_atr_high_vol_pct, min_v=0.5, max_v=10.0)

        # Backtest
        _require_range("BACKTEST_CAPITAL", self.backtest_capital, min_v=100.0, max_v=10_000_000.0)
        _require_range("BACKTEST_LOOKBACK_DAYS", self.backtest_lookback_days, min_v=30, max_v=2000)
        _require_range("BACKTEST_RISK_PCT", self.backtest_risk_pct, min_v=0.0, max_v=0.10)

    def has_email_transport(self) -> bool:
        """True iff at least one email transport is configured."""
        return bool(
            (self.gmail_oauth_client_id and self.gmail_oauth_client_secret
             and self.gmail_oauth_refresh_token)
            or self.resend_api_key
            or self.gmail_app_password
        )

    def safe_dict(self) -> dict[str, Any]:
        """Return a dict suitable for logging — secret fields are redacted."""
        secret_keys = {
            "alpaca_api_key", "alpaca_secret_key", "anthropic_api_key",
            "perplexity_api_key", "gmail_oauth_client_id",
            "gmail_oauth_client_secret", "gmail_oauth_refresh_token",
            "gmail_app_password", "resend_api_key",
        }
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in secret_keys:
                out[f.name] = "<set>" if value else "<unset>"
            else:
                out[f.name] = value
        return out


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_from_env() -> Settings:
    """Read every supported env var into a Settings instance. Pure function."""
    return Settings(
        # Risk / sizing
        max_positions=_env_int("MAX_POSITIONS", 6),
        max_position_pct=_env_float("MAX_POSITION_PCT", 0.20),
        max_weekly_trades=_env_int("MAX_WEEKLY_TRADES", 3),
        min_cash_buffer_pct=_env_float("MIN_CASH_BUFFER_PCT", 0.15),
        circuit_breaker_pct=_env_float("CIRCUIT_BREAKER_PCT", 0.15),
        max_sector_failures=_env_int("MAX_SECTOR_FAILURES", 2),
        max_sector_concentration=_env_int("MAX_SECTOR_CONCENTRATION", 3),
        min_momentum_score=_env_float("MIN_MOMENTUM_SCORE", 40.0),
        risk_per_trade_pct=_env_float("RISK_PER_TRADE_PCT", 0.01),
        kelly_fraction=_env_float("KELLY_FRACTION", 0.25),

        # Stops / exits
        hard_stop_pct=_env_float("HARD_STOP_PCT", -0.07),
        atr_stop_multiple=_env_float("ATR_STOP_MULTIPLE", 2.0),
        atr_trail_multiple=_env_float("ATR_TRAIL_MULTIPLE", 3.0),
        trail_pct_min=_env_float("TRAIL_PCT_MIN", 5.0),
        trail_pct_max=_env_float("TRAIL_PCT_MAX", 15.0),
        time_decay_days=_env_int("TIME_DECAY_DAYS", 14),
        time_decay_min_move_pct=_env_float("TIME_DECAY_MIN_MOVE_PCT", 3.0),
        vol_expansion_threshold=_env_float("VOL_EXPANSION_THRESHOLD", 2.0),

        # Multi-agent debate
        debate_rounds=_env_int("SHARK_DEBATE_ROUNDS", 1),
        llm_risk_review=_env_str("SHARK_LLM_RISK_REVIEW", "false").lower() in ("true", "1", "yes"),
        risk_debate_rounds=_env_int("SHARK_RISK_DEBATE_ROUNDS", 1),

        # Regime
        regime_atr_high_vol_pct=_env_float("REGIME_ATR_HIGH_VOL_PCT", 2.5),
        regime_benchmark=_env_str("REGIME_BENCHMARK", "SPY"),

        # Backtest
        backtest_capital=_env_float("BACKTEST_CAPITAL", 10_000.0),
        backtest_lookback_days=_env_int("BACKTEST_LOOKBACK_DAYS", 365),
        backtest_risk_pct=_env_float("BACKTEST_RISK_PCT", 0.01),
        backtest_atr_stop_mult=_env_float("BACKTEST_ATR_STOP_MULT", 2.0),
        backtest_momentum_min=_env_float("BACKTEST_MOMENTUM_MIN", 50.0),
        backtest_rs_min=_env_float("BACKTEST_RS_MIN", 0.0),
        backtest_symbols=_env_str("BACKTEST_SYMBOLS", ""),

        # Trading mode
        trading_mode=_env_str("TRADING_MODE", "paper").lower(),
        alpaca_base_url=_env_str("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),

        # Paper-mode overrides
        paper_bear_override=_env_str("PAPER_BEAR_OVERRIDE", "true").lower() in ("true", "1", "yes"),
        paper_macro_bypass=_env_str("PAPER_MACRO_BYPASS", "true").lower() in ("true", "1", "yes"),
        paper_bear_max_trades=_env_int("PAPER_BEAR_MAX_TRADES", 1),
        paper_bear_size_mult=_env_float("PAPER_BEAR_SIZE_MULT", 0.5),
        paper_bear_confidence=_env_float("PAPER_BEAR_CONFIDENCE", 0.85),
        paper_bear_min_score=_env_int("PAPER_BEAR_MIN_SCORE", 3),

        # API credentials
        alpaca_api_key=_env_str("ALPACA_API_KEY"),
        alpaca_secret_key=_env_str("ALPACA_SECRET_KEY"),
        alpaca_data_feed=_env_str("ALPACA_DATA_FEED", "iex"),
        anthropic_api_key=_env_str("ANTHROPIC_API_KEY"),
        claude_model=_env_str("CLAUDE_MODEL", "claude-sonnet-4-5"),
        perplexity_api_key=_env_str("PERPLEXITY_API_KEY"),

        # Notifications
        notify_email=_env_str("NOTIFY_EMAIL"),
        notify_from_email=_env_str("NOTIFY_FROM_EMAIL"),
        gmail_oauth_client_id=_env_str("GMAIL_OAUTH_CLIENT_ID"),
        gmail_oauth_client_secret=_env_str("GMAIL_OAUTH_CLIENT_SECRET"),
        gmail_oauth_refresh_token=_env_str("GMAIL_OAUTH_REFRESH_TOKEN"),
        gmail_app_password=_env_str("GMAIL_APP_PASSWORD"),
        resend_api_key=_env_str("RESEND_API_KEY"),
        resend_from_email=_env_str("RESEND_FROM_EMAIL"),
    )


_cached_settings: Optional[Settings] = None


def load_settings(*, force_reload: bool = False) -> Settings:
    """Return validated Settings. Caches the result; pass force_reload=True
    in tests that mutate os.environ between assertions.

    Raises:
        ConfigError: if any required env var is malformed or out of range.
    """
    global _cached_settings
    if _cached_settings is not None and not force_reload:
        return _cached_settings
    settings = _load_from_env()
    settings.validate()
    _cached_settings = settings
    return settings


def get_settings() -> Settings:
    """Lazy accessor — returns the cached Settings, loading on first call."""
    return load_settings()


__all__ = [
    "ConfigError",
    "Settings",
    "load_settings",
    "get_settings",
]
