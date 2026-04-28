"""
Tests for shark.execution.exit_manager — exit decision logic.

Covers: hard stop, partial profit, time decay, regime shift,
volatility expansion, dynamic stop, edge cases.
"""

import importlib
from datetime import date, timedelta

import pytest


def _load_mod():
    import shark.execution.exit_manager as mod
    importlib.reload(mod)
    return mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("HARD_STOP_PCT", "TIME_DECAY_DAYS", "TIME_DECAY_MIN_MOVE_PCT", "VOL_EXPANSION_THRESHOLD"):
        monkeypatch.delenv(k, raising=False)


def _pos(symbol="AAPL", qty=30, plpc=-0.01, price=105.0, entry=100.0):
    return {
        "symbol": symbol,
        "qty": qty,
        "unrealized_plpc": plpc,
        "current_price": price,
        "avg_entry_price": entry,
    }


# ---------------------------------------------------------------------------
# evaluate_exits — hard stop
# ---------------------------------------------------------------------------

class TestHardStop:
    def test_triggers_at_threshold(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(plpc=-0.07)])
        assert len(actions) == 1
        assert actions[0]["action"] == "CLOSE_ALL"
        assert actions[0]["priority"] == 1

    def test_triggers_beyond_threshold(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(plpc=-0.15)])
        assert actions[0]["urgency"] == "IMMEDIATE"

    def test_no_trigger_above_threshold(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(plpc=-0.05)])
        # No hard stop action (might be others)
        hard = [a for a in actions if a["priority"] == 1]
        assert len(hard) == 0


# ---------------------------------------------------------------------------
# evaluate_exits — partial profit
# ---------------------------------------------------------------------------

class TestPartialProfit:
    def test_tier1_at_1r(self):
        mod = _load_mod()
        # entry=100, hard_stop=7%, so risk_per_share~=7.0
        # At price=108 → current_r = 8/7 ≈ 1.14 → tier1
        actions = mod.evaluate_exits([_pos(qty=30, plpc=0.08, price=108.0, entry=100.0)])
        partials = [a for a in actions if a["action"] == "PARTIAL_SELL"]
        assert len(partials) == 1
        assert partials[0]["tier"] == 1

    def test_tier2_at_2r(self):
        mod = _load_mod()
        # price=115 → current_r = 15/7 ≈ 2.14 → both tiers fire, first wins
        actions = mod.evaluate_exits([_pos(qty=30, plpc=0.15, price=115.0, entry=100.0)])
        partials = [a for a in actions if a["action"] == "PARTIAL_SELL"]
        assert len(partials) >= 1

    def test_no_partial_below_1r(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(qty=30, plpc=0.03, price=103.0, entry=100.0)])
        partials = [a for a in actions if a["action"] == "PARTIAL_SELL"]
        assert len(partials) == 0

    def test_no_partial_under_3_shares(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(qty=2, plpc=0.10, price=110.0, entry=100.0)])
        partials = [a for a in actions if a["action"] == "PARTIAL_SELL"]
        assert len(partials) == 0


# ---------------------------------------------------------------------------
# evaluate_exits — time decay
# ---------------------------------------------------------------------------

class TestTimeDecay:
    def _trade_log(self, symbol, days_ago):
        return [{
            "symbol": symbol,
            "side": "buy",
            "date": (date.today() - timedelta(days=days_ago)).isoformat(),
        }]

    def test_triggers_after_threshold(self):
        mod = _load_mod()
        positions = [_pos(plpc=0.01)]  # 1% move (below 2% min)
        log = self._trade_log("AAPL", 6)
        actions = mod.evaluate_exits(positions, trade_log=log)
        decays = [a for a in actions if "time decay" in a.get("reason", "").lower()]
        assert len(decays) == 1

    def test_no_trigger_when_moved_enough(self):
        mod = _load_mod()
        positions = [_pos(plpc=0.05)]  # 5% move (above 2% min)
        log = self._trade_log("AAPL", 10)
        actions = mod.evaluate_exits(positions, trade_log=log)
        decays = [a for a in actions if "time decay" in a.get("reason", "").lower()]
        assert len(decays) == 0

    def test_no_trigger_fresh_position(self):
        mod = _load_mod()
        positions = [_pos(plpc=0.005)]
        log = self._trade_log("AAPL", 2)
        actions = mod.evaluate_exits(positions, trade_log=log)
        decays = [a for a in actions if "time decay" in a.get("reason", "").lower()]
        assert len(decays) == 0


# ---------------------------------------------------------------------------
# evaluate_exits — regime shift
# ---------------------------------------------------------------------------

class TestRegimeShift:
    def test_bear_triggers_close(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos()], regime="BEAR_QUIET")
        bears = [a for a in actions if "regime" in a.get("reason", "").lower()]
        assert len(bears) == 1
        assert bears[0]["action"] == "CLOSE_ALL"

    def test_bull_no_regime_close(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(plpc=0.0)], regime="BULL_QUIET")
        bears = [a for a in actions if "regime" in a.get("reason", "").lower()]
        assert len(bears) == 0


# ---------------------------------------------------------------------------
# evaluate_exits — edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_positions(self):
        mod = _load_mod()
        assert mod.evaluate_exits([]) == []

    def test_zero_qty_skipped(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(qty=0)])
        assert actions == []

    def test_multiple_positions(self):
        mod = _load_mod()
        actions = mod.evaluate_exits([
            _pos("AAPL", plpc=-0.10),
            _pos("MSFT", plpc=-0.10),
        ])
        assert len(actions) == 2

    def test_hard_stop_wins_over_other_signals(self):
        """When both hard stop and regime shift fire, hard stop (priority 1) wins."""
        mod = _load_mod()
        actions = mod.evaluate_exits([_pos(plpc=-0.10)], regime="BEAR_VOLATILE")
        assert actions[0]["priority"] == 1


# ---------------------------------------------------------------------------
# check_volatility_expansion
# ---------------------------------------------------------------------------

class TestVolatilityExpansion:
    def test_triggers_at_threshold(self):
        mod = _load_mod()
        result = mod.check_volatility_expansion("AAPL", 4.0, 2.0)  # 2x
        assert result is not None
        assert result["action"] == "TIGHTEN_STOP"

    def test_no_trigger_below_threshold(self):
        mod = _load_mod()
        result = mod.check_volatility_expansion("AAPL", 3.5, 2.0)  # 1.75x
        assert result is None

    def test_zero_entry_atr(self):
        mod = _load_mod()
        assert mod.check_volatility_expansion("AAPL", 2.0, 0.0) is None


# ---------------------------------------------------------------------------
# compute_dynamic_stop
# ---------------------------------------------------------------------------

class TestDynamicStop:
    def test_bull_quiet_standard(self):
        mod = _load_mod()
        result = mod.compute_dynamic_stop(100.0, 105.0, 2.0, 5.0, "BULL_QUIET")
        assert result["trail_pct"] == 10.0
        assert result["stop_price"] > 0

    def test_bull_volatile_tighter(self):
        mod = _load_mod()
        result = mod.compute_dynamic_stop(100.0, 105.0, 2.0, 5.0, "BULL_VOLATILE")
        assert result["trail_pct"] == 8.0

    def test_bear_aggressive(self):
        mod = _load_mod()
        result = mod.compute_dynamic_stop(100.0, 105.0, 2.0, 5.0, "BEAR_QUIET")
        assert result["trail_pct"] == 5.0

    def test_profitable_stop_never_below_entry(self):
        mod = _load_mod()
        result = mod.compute_dynamic_stop(100.0, 105.0, 10.0, 5.0, "BULL_QUIET")
        # ATR-based: 105 - 20 = 85, pct-based: 105*0.90=94.5
        # Higher = 94.5, but profit > 0, so clamped to entry=100
        assert result["stop_price"] >= 100.0

    def test_large_profit_tightens(self):
        mod = _load_mod()
        r1 = mod.compute_dynamic_stop(100.0, 120.0, 2.0, 5.0, "BULL_QUIET")
        r2 = mod.compute_dynamic_stop(100.0, 120.0, 2.0, 25.0, "BULL_QUIET")
        assert r2["trail_pct"] < r1["trail_pct"]  # tighter at higher profit
