"""
Tests for shark.execution.position_sizer — money-critical sizing logic.

Covers: ATR sizing, Kelly sizing, regime adjustment, drawdown scaling,
confidence scaling, circuit breaker, partial exit plan, edge cases.
"""

import os
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers — reset module-level globals between tests
# ---------------------------------------------------------------------------

def _load_sizer():
    """Import (or reimport) the position_sizer module with fresh env."""
    import importlib
    import shark.execution.position_sizer as mod
    importlib.reload(mod)
    return mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure env-backed globals start from known defaults."""
    for k in ("RISK_PER_TRADE_PCT", "ATR_STOP_MULTIPLE", "MAX_POSITION_PCT", "KELLY_FRACTION"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# compute_position_size
# ---------------------------------------------------------------------------

class TestComputePositionSize:
    """Core sizing function."""

    def test_basic_sizing_returns_positive(self):
        mod = _load_sizer()
        result = mod.compute_position_size(
            portfolio_value=100_000,
            current_price=50.0,
            atr=2.0,
        )
        assert result["shares"] > 0
        assert result["dollar_amount"] > 0
        assert result["stop_price"] > 0
        assert result["risk_dollars"] > 0

    def test_zero_portfolio_returns_zero(self):
        mod = _load_sizer()
        result = mod.compute_position_size(0.0, 50.0, 2.0)
        assert result["shares"] == 0
        assert result["method_used"] == "blocked"

    def test_zero_price_returns_zero(self):
        mod = _load_sizer()
        result = mod.compute_position_size(100_000, 0.0, 2.0)
        assert result["shares"] == 0

    def test_negative_portfolio_returns_zero(self):
        mod = _load_sizer()
        result = mod.compute_position_size(-10_000, 50.0, 2.0)
        assert result["shares"] == 0

    def test_bear_regime_blocks_trade(self):
        mod = _load_sizer()
        result = mod.compute_position_size(
            100_000, 50.0, 2.0, regime_multiplier=0.0,
        )
        assert result["shares"] == 0
        assert "regime" in result["sizing_details"].get("reason", "").lower()

    def test_half_regime_reduces_size(self):
        mod = _load_sizer()
        full = mod.compute_position_size(100_000, 50.0, 2.0, regime_multiplier=1.0)
        half = mod.compute_position_size(100_000, 50.0, 2.0, regime_multiplier=0.5)
        assert half["shares"] <= full["shares"]

    def test_higher_atr_fewer_shares(self):
        """More volatile stocks should get fewer shares."""
        mod = _load_sizer()
        low_vol = mod.compute_position_size(100_000, 50.0, 1.0)
        high_vol = mod.compute_position_size(100_000, 50.0, 5.0)
        assert high_vol["shares"] < low_vol["shares"]

    def test_stop_distance_minimum_2pct(self):
        """Even with tiny ATR, stop must be at least 2% of price."""
        mod = _load_sizer()
        result = mod.compute_position_size(100_000, 100.0, 0.01)
        assert result["stop_distance"] >= 2.0  # 2% of $100

    def test_position_never_exceeds_max_pct(self):
        mod = _load_sizer()
        result = mod.compute_position_size(
            portfolio_value=100_000,
            current_price=10.0,
            atr=0.1,  # tiny ATR → lots of ATR shares
        )
        max_pct = 20.0  # default MAX_POSITION_FRAC = 0.20 = 20%
        assert result["position_pct"] <= max_pct + 0.01  # small float tolerance

    def test_method_used_reflects_binding_constraint(self):
        mod = _load_sizer()
        result = mod.compute_position_size(100_000, 50.0, 2.0)
        assert result["method_used"] in ("atr", "kelly", "max_cap")

    def test_confidence_scaling(self):
        """Higher confidence → more shares."""
        mod = _load_sizer()
        low_conf = mod.compute_position_size(100_000, 50.0, 2.0, confidence=0.70)
        high_conf = mod.compute_position_size(100_000, 50.0, 2.0, confidence=1.0)
        assert high_conf["shares"] >= low_conf["shares"]

    def test_zero_atr_uses_fallback_stop(self):
        mod = _load_sizer()
        result = mod.compute_position_size(100_000, 100.0, 0.0)
        # Fallback: 10% of price = $10 stop distance, then clamped to 2% min
        assert result["shares"] > 0
        assert result["stop_distance"] > 0


# ---------------------------------------------------------------------------
# Circuit breaker (drawdown scaling)
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """Drawdown scaling and circuit breaker cutoff."""

    def test_no_drawdown_full_size(self):
        mod = _load_sizer()
        result = mod.compute_position_size(
            100_000, 50.0, 2.0, peak_equity=100_000,
        )
        assert result["sizing_details"]["drawdown_mult"] == 1.0

    def test_mild_drawdown_reduces(self):
        mod = _load_sizer()
        result = mod.compute_position_size(
            95_000, 50.0, 2.0, peak_equity=100_000,  # 5% drawdown
        )
        assert result["sizing_details"]["drawdown_mult"] < 1.0
        assert result["shares"] > 0  # not blocked yet

    def test_severe_drawdown_blocks_trade(self):
        """Drawdown > 15% must trigger circuit breaker → 0 shares."""
        mod = _load_sizer()
        result = mod.compute_position_size(
            80_000, 50.0, 2.0, peak_equity=100_000,  # 20% drawdown
        )
        assert result["shares"] == 0
        assert "circuit breaker" in result["sizing_details"].get("reason", "").lower()

    def test_exactly_15pct_drawdown_still_trades(self):
        """At exactly 15%, drawdown_mult > 0 (boundary is > 15%)."""
        mod = _load_sizer()
        result = mod.compute_position_size(
            85_000, 50.0, 2.0, peak_equity=100_000,  # 15% exactly
        )
        # At 15% drawdown_mult = 0.50 - (15-10)*0.04 = 0.50 - 0.20 = 0.30
        assert result["sizing_details"]["drawdown_mult"] > 0

    def test_just_over_15pct_blocks(self):
        mod = _load_sizer()
        result = mod.compute_position_size(
            84_900, 50.0, 2.0, peak_equity=100_000,  # 15.1%
        )
        assert result["shares"] == 0

    def test_peak_zero_treated_as_no_drawdown(self):
        mod = _load_sizer()
        result = mod.compute_position_size(
            100_000, 50.0, 2.0, peak_equity=0.0,
        )
        assert result["sizing_details"]["drawdown_mult"] == 1.0


# ---------------------------------------------------------------------------
# _compute_drawdown_multiplier (internal)
# ---------------------------------------------------------------------------

class TestDrawdownMultiplier:
    def test_above_peak(self):
        mod = _load_sizer()
        assert mod._compute_drawdown_multiplier(110_000, 100_000) == 1.0

    def test_at_peak(self):
        mod = _load_sizer()
        assert mod._compute_drawdown_multiplier(100_000, 100_000) == 1.0

    def test_3pct(self):
        mod = _load_sizer()
        assert mod._compute_drawdown_multiplier(97_000, 100_000) == 1.0

    def test_5pct(self):
        mod = _load_sizer()
        assert mod._compute_drawdown_multiplier(95_000, 100_000) == 0.90

    def test_10pct(self):
        mod = _load_sizer()
        m = mod._compute_drawdown_multiplier(90_000, 100_000)
        assert 0.48 <= m <= 0.52  # ~0.50

    def test_over_15pct_returns_zero(self):
        mod = _load_sizer()
        assert mod._compute_drawdown_multiplier(84_000, 100_000) == 0.0


# ---------------------------------------------------------------------------
# _compute_kelly (internal)
# ---------------------------------------------------------------------------

class TestKelly:
    def test_positive_edge(self):
        mod = _load_sizer()
        k = mod._compute_kelly(0.55, 2.0)
        assert 0.01 <= k <= 0.20

    def test_no_edge(self):
        """50/50 coin flip with 1:1 payoff → Kelly = 0, clamped to 1%."""
        mod = _load_sizer()
        k = mod._compute_kelly(0.50, 1.0)
        assert k == 0.01

    def test_zero_win_rate(self):
        mod = _load_sizer()
        k = mod._compute_kelly(0.0, 2.0)
        assert k == 0.01

    def test_negative_ratio(self):
        mod = _load_sizer()
        k = mod._compute_kelly(0.50, -1.0)
        assert k == 0.01

    def test_max_capped(self):
        """Even a perfect system is capped at MAX_POSITION_FRAC."""
        mod = _load_sizer()
        k = mod._compute_kelly(0.99, 10.0)
        assert k <= 0.20 + 0.001  # default MAX_POSITION_FRAC


# ---------------------------------------------------------------------------
# compute_partial_exit_plan
# ---------------------------------------------------------------------------

class TestPartialExitPlan:
    def test_shares_sum(self):
        mod = _load_sizer()
        plan = mod.compute_partial_exit_plan(90, 100.0, 95.0, 120.0)
        total = sum(t["shares"] for t in plan["tiers"])
        assert total == 90

    def test_tier_prices_ascending(self):
        mod = _load_sizer()
        plan = mod.compute_partial_exit_plan(90, 100.0, 95.0, 120.0)
        prices = [t["target_price"] for t in plan["tiers"]]
        assert prices[0] < prices[1]

    def test_min_1_share_per_tier(self):
        mod = _load_sizer()
        plan = mod.compute_partial_exit_plan(2, 50.0, 45.0, 70.0)
        for tier in plan["tiers"]:
            # tier3 gets remainder: 2 - 1 - 1 = 0 is ok
            pass
        # At least tiers 1 and 2 have >= 1
        assert plan["tiers"][0]["shares"] >= 1
        assert plan["tiers"][1]["shares"] >= 1

    def test_negative_risk_uses_fallback(self):
        """If stop > entry (should not happen), fallback to 5%."""
        mod = _load_sizer()
        plan = mod.compute_partial_exit_plan(30, 100.0, 105.0, 120.0)
        # risk = 100 - 105 = -5 → fallback to 100*0.05 = 5
        assert plan["tiers"][0]["target_price"] == 105.0  # entry + 5


# ---------------------------------------------------------------------------
# Env var integration
# ---------------------------------------------------------------------------

class TestEnvConfig:
    def test_custom_risk_frac(self, monkeypatch):
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "0.005")  # 0.5%
        mod = _load_sizer()
        assert mod._BASE_RISK_FRAC == 0.005

    def test_custom_max_position(self, monkeypatch):
        monkeypatch.setenv("MAX_POSITION_PCT", "0.10")  # 10%
        mod = _load_sizer()
        assert mod._MAX_POSITION_FRAC == 0.10
