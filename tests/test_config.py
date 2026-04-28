"""
Tests for shark.config — central configuration validation.

Verifies that out-of-range values fail fast and that defaults load cleanly.
"""

import os
import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the cached settings between tests."""
    import shark.config as cfg
    cfg._cached_settings = None
    yield
    cfg._cached_settings = None


@pytest.fixture
def _minimal_env(monkeypatch):
    """Set minimum required env vars for a valid Settings load."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-perplexity")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_loads_with_defaults(self, _minimal_env):
        from shark.config import load_settings
        s = load_settings(force_reload=True)
        assert s.max_positions == 6
        assert s.max_position_pct == 0.20
        assert s.risk_per_trade_pct == 0.01
        assert s.hard_stop_pct == -0.07

    def test_safe_dict_redacts_secrets(self, _minimal_env):
        from shark.config import load_settings
        s = load_settings(force_reload=True)
        d = s.safe_dict()
        assert d["alpaca_api_key"] == "<set>"
        assert d["max_positions"] == 6

    def test_caching(self, _minimal_env):
        from shark.config import load_settings
        s1 = load_settings(force_reload=True)
        s2 = load_settings()
        assert s1 is s2

    def test_force_reload(self, _minimal_env, monkeypatch):
        from shark.config import load_settings
        s1 = load_settings(force_reload=True)
        monkeypatch.setenv("MAX_POSITIONS", "8")
        s2 = load_settings(force_reload=True)
        assert s2.max_positions == 8


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

class TestValidation:
    def test_max_positions_too_high(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("MAX_POSITIONS", "50")
        with pytest.raises(ConfigError, match="MAX_POSITIONS"):
            load_settings(force_reload=True)

    def test_max_position_pct_too_high(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("MAX_POSITION_PCT", "0.90")
        with pytest.raises(ConfigError, match="MAX_POSITION_PCT"):
            load_settings(force_reload=True)

    def test_circuit_breaker_too_high(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("CIRCUIT_BREAKER_PCT", "0.80")
        with pytest.raises(ConfigError, match="CIRCUIT_BREAKER_PCT"):
            load_settings(force_reload=True)

    def test_hard_stop_must_be_negative(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("HARD_STOP_PCT", "0.07")
        with pytest.raises(ConfigError, match="HARD_STOP_PCT"):
            load_settings(force_reload=True)

    def test_trail_min_must_be_less_than_max(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("TRAIL_PCT_MIN", "20.0")
        monkeypatch.setenv("TRAIL_PCT_MAX", "10.0")
        with pytest.raises(ConfigError, match="TRAIL_PCT_MIN"):
            load_settings(force_reload=True)

    def test_risk_per_trade_too_high(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "0.50")
        with pytest.raises(ConfigError, match="RISK_PER_TRADE_PCT"):
            load_settings(force_reload=True)

    def test_non_numeric_raises(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("MAX_POSITIONS", "abc")
        with pytest.raises(ConfigError, match="not a valid int"):
            load_settings(force_reload=True)

    def test_float_env_non_numeric(self, _minimal_env, monkeypatch):
        from shark.config import load_settings, ConfigError
        monkeypatch.setenv("CIRCUIT_BREAKER_PCT", "notanumber")
        with pytest.raises(ConfigError, match="not a valid float"):
            load_settings(force_reload=True)


# ---------------------------------------------------------------------------
# Email transport detection
# ---------------------------------------------------------------------------

class TestEmailTransport:
    def test_no_transport(self, _minimal_env):
        from shark.config import load_settings
        s = load_settings(force_reload=True)
        assert s.has_email_transport() is False

    def test_resend_transport(self, _minimal_env, monkeypatch):
        from shark.config import load_settings
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        s = load_settings(force_reload=True)
        assert s.has_email_transport() is True

    def test_gmail_app_password_transport(self, _minimal_env, monkeypatch):
        from shark.config import load_settings
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "pass123")
        s = load_settings(force_reload=True)
        assert s.has_email_transport() is True
