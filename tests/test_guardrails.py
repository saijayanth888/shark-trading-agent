import os
from unittest.mock import patch

import pytest

_NORMAL_MACRO = {
    "impact_level": "NORMAL",
    "rules": {"new_trades_allowed": True, "position_size_multiplier": 1.0, "description": "Normal"},
    "events_today": [],
    "events_nearby": [],
    "description": "No major macro events nearby",
    "check_date": "2025-01-15",
}


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for key in ["MAX_POSITIONS", "MAX_POSITION_PCT", "MAX_WEEKLY_TRADES",
                "MIN_CASH_BUFFER_PCT", "CIRCUIT_BREAKER_PCT", "MAX_SECTOR_FAILURES"]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _mock_macro():
    """Prevent date-dependent FOMC/CPI blocks from making tests flaky."""
    with patch("shark.execution.guardrails.check_macro_calendar", return_value=_NORMAL_MACRO):
        yield


def make_guardrails():
    from shark.execution.guardrails import Guardrails
    return Guardrails()


class TestMaxPositions:
    def test_passes_under_limit(self):
        g = make_guardrails()
        ok, msg = g.check_max_positions(3)
        assert ok

    def test_fails_when_full(self):
        g = make_guardrails()
        ok, msg = g.check_max_positions(6)
        assert not ok
        assert "limit" in msg.lower() or "6" in msg

    def test_fails_over_limit(self):
        g = make_guardrails()
        ok, msg = g.check_max_positions(7)
        assert not ok

    def test_zero_positions_passes(self):
        g = make_guardrails()
        ok, _ = g.check_max_positions(0)
        assert ok


class TestPositionSize:
    def test_passes_at_20pct(self):
        g = make_guardrails()
        ok, _ = g.check_position_size(2000.0, 10000.0)
        assert ok

    def test_fails_over_20pct(self):
        g = make_guardrails()
        ok, _ = g.check_position_size(2100.0, 10000.0)
        assert not ok

    def test_passes_well_under_limit(self):
        g = make_guardrails()
        ok, _ = g.check_position_size(1000.0, 10000.0)
        assert ok

    def test_fails_on_zero_portfolio(self):
        g = make_guardrails()
        ok, _ = g.check_position_size(500.0, 0.0)
        assert not ok


class TestWeeklyTradeCount:
    def test_passes_under_limit(self):
        g = make_guardrails()
        ok, _ = g.check_weekly_trade_count(2)
        assert ok

    def test_fails_at_limit(self):
        g = make_guardrails()
        ok, _ = g.check_weekly_trade_count(3)
        assert not ok

    def test_fails_over_limit(self):
        g = make_guardrails()
        ok, _ = g.check_weekly_trade_count(5)
        assert not ok

    def test_passes_zero_trades(self):
        g = make_guardrails()
        ok, _ = g.check_weekly_trade_count(0)
        assert ok


class TestCashBuffer:
    def test_passes_with_healthy_buffer(self):
        g = make_guardrails()
        # After spending $1000 from $3000, $2000 remains = 20% of $10000
        ok, _ = g.check_cash_buffer(cash_after_trade=2000.0, portfolio_value=10000.0)
        assert ok

    def test_fails_when_buffer_too_low(self):
        g = make_guardrails()
        # $500 after trade = 5% of $10000, below 15% minimum
        ok, _ = g.check_cash_buffer(cash_after_trade=500.0, portfolio_value=10000.0)
        assert not ok

    def test_passes_exactly_at_15pct(self):
        g = make_guardrails()
        ok, _ = g.check_cash_buffer(cash_after_trade=1500.0, portfolio_value=10000.0)
        assert ok


class TestCircuitBreaker:
    def test_inactive_when_healthy(self):
        g = make_guardrails()
        ok, _ = g.check_circuit_breaker(current_equity=9500.0, peak_equity=10000.0)
        assert ok

    def test_triggers_at_15pct_drawdown(self):
        g = make_guardrails()
        ok, msg = g.check_circuit_breaker(current_equity=8400.0, peak_equity=10000.0)
        assert not ok
        assert "circuit" in msg.lower() or "breaker" in msg.lower()

    def test_triggers_large_drawdown(self):
        g = make_guardrails()
        ok, _ = g.check_circuit_breaker(current_equity=5000.0, peak_equity=10000.0)
        assert not ok

    def test_bootstraps_on_zero_peak_with_positive_equity(self):
        g = make_guardrails()
        ok, msg = g.check_circuit_breaker(current_equity=1000.0, peak_equity=0.0)
        assert ok  # bootstraps from current equity instead of blocking
        assert "baseline" in msg

    def test_fails_on_zero_peak_and_zero_equity(self):
        g = make_guardrails()
        ok, _ = g.check_circuit_breaker(current_equity=0.0, peak_equity=0.0)
        assert not ok


class TestSectorFailures:
    def _make_trade(self, sector, result):
        return {"sector": sector, "result": result}

    def test_passes_with_no_failures(self):
        g = make_guardrails()
        ok, _ = g.check_sector_failures("Technology", [])
        assert ok

    def test_passes_with_one_failure(self):
        g = make_guardrails()
        trades = [self._make_trade("Technology", "loss")]
        ok, _ = g.check_sector_failures("Technology", trades)
        assert ok

    def test_fails_with_two_consecutive_failures(self):
        g = make_guardrails()
        trades = [
            self._make_trade("Technology", "loss"),
            self._make_trade("Technology", "loss"),
        ]
        ok, _ = g.check_sector_failures("Technology", trades)
        assert not ok

    def test_win_resets_consecutive_count(self):
        g = make_guardrails()
        # Most recent first: loss, win, loss — streak is only 1
        trades = [
            self._make_trade("Technology", "loss"),
            self._make_trade("Technology", "win"),
            self._make_trade("Technology", "loss"),
        ]
        ok, _ = g.check_sector_failures("Technology", trades)
        assert ok

    def test_different_sector_ignored(self):
        g = make_guardrails()
        trades = [
            self._make_trade("Financials", "loss"),
            self._make_trade("Financials", "loss"),
        ]
        ok, _ = g.check_sector_failures("Technology", trades)
        assert ok


class TestRunAll:
    def _make_trade(self, symbol="AAPL", qty=10, cost=1500.0, sector="Technology"):
        return {"symbol": symbol, "qty": qty, "estimated_cost": cost, "sector": sector}

    def _make_account(self, value=10000.0, cash=3000.0, positions=2):
        return {
            "portfolio_value": value,
            "cash": cash,
            "positions": [{"symbol": f"T{i}"} for i in range(positions)],
        }

    def test_all_pass(self):
        g = make_guardrails()
        result = g.run_all(
            proposed_trade=self._make_trade(),
            account=self._make_account(),
            weekly_count=1,
            peak_equity=10000.0,
            recent_trades=[],
        )
        assert result["approved"] is True
        assert result["violations"] == []
        assert "checks" in result

    def test_fails_with_too_many_positions(self):
        g = make_guardrails()
        result = g.run_all(
            proposed_trade=self._make_trade(),
            account=self._make_account(positions=6),
            weekly_count=1,
            peak_equity=10000.0,
            recent_trades=[],
        )
        assert result["approved"] is False
        assert len(result["violations"]) >= 1

    def test_collects_multiple_violations(self):
        g = make_guardrails()
        result = g.run_all(
            proposed_trade=self._make_trade(cost=9500.0, qty=95),
            account=self._make_account(value=10000.0, cash=500.0, positions=6),
            weekly_count=4,
            peak_equity=10000.0,
            recent_trades=[],
        )
        assert result["approved"] is False
        assert len(result["violations"]) >= 3

    def test_adjusted_size_calculated_on_oversized_trade(self):
        g = make_guardrails()
        # 30% position — over the 20% limit
        result = g.run_all(
            proposed_trade=self._make_trade(cost=3000.0, qty=30),
            account=self._make_account(value=10000.0, cash=4000.0, positions=2),
            weekly_count=1,
            peak_equity=10000.0,
            recent_trades=[],
        )
        # adjusted_size should be reduced to fit within 20%
        assert result["adjusted_size"] < 30

    def test_circuit_breaker_violation(self):
        g = make_guardrails()
        result = g.run_all(
            proposed_trade=self._make_trade(),
            account=self._make_account(value=8000.0, cash=3000.0),
            weekly_count=1,
            peak_equity=10000.0,
            recent_trades=[],
        )
        assert result["approved"] is False
        assert any("circuit" in v.lower() for v in result["violations"])
