"""
Tests for shark.execution.stops — trailing stop tightening logic.

All Alpaca API calls are mocked. Tests verify decision logic, safety
guardrails, and error-recovery paths.
"""

from unittest.mock import MagicMock, patch, call
import pytest


def _pos(symbol="AAPL", qty=30, price=110.0, plpc=0.10):
    return {
        "symbol": symbol,
        "qty": qty,
        "current_price": price,
        "unrealized_plpc": plpc,
    }


# Patch targets
_GET_CLIENT = "shark.execution.stops._get_client"
_GET_EXISTING = "shark.execution.stops._get_existing_trailing_stop"
_PLACE_STOP = "shark.execution.stops.place_trailing_stop"
_CANCEL = "shark.execution.stops.cancel_order"


# ---------------------------------------------------------------------------
# Tightening decisions
# ---------------------------------------------------------------------------

class TestTighteningDecisions:
    """Verify the profit-tier → trail-pct mapping."""

    @patch(_CANCEL, return_value=True)
    @patch(_PLACE_STOP, return_value={"order_id": "new123"})
    @patch(_GET_EXISTING, return_value=(10.0, "old123"))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_20pct_gain_tightens_to_5(self, _c, _e, _p, _x):
        from shark.execution.stops import manage_stops
        actions = manage_stops([_pos(plpc=0.22)])
        assert actions[0]["action"] == "tightened"
        assert actions[0]["new_trail_pct"] == 5.0

    @patch(_CANCEL, return_value=True)
    @patch(_PLACE_STOP, return_value={"order_id": "new123"})
    @patch(_GET_EXISTING, return_value=(10.0, "old123"))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_15pct_gain_tightens_to_7(self, _c, _e, _p, _x):
        from shark.execution.stops import manage_stops
        actions = manage_stops([_pos(plpc=0.16)])
        assert actions[0]["action"] == "tightened"
        assert actions[0]["new_trail_pct"] == 7.0

    @patch(_GET_EXISTING, return_value=(10.0, "old123"))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_below_15pct_skipped(self, _c, _e):
        from shark.execution.stops import manage_stops
        actions = manage_stops([_pos(plpc=0.10)])
        assert actions[0]["action"] == "skipped"
        assert "below 15%" in actions[0]["reason"].lower() or "default" in actions[0]["reason"].lower()


# ---------------------------------------------------------------------------
# Never-loosen guardrail
# ---------------------------------------------------------------------------

class TestNeverLoosen:
    @patch(_GET_EXISTING, return_value=(4.0, "ord456"))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_already_tighter_skips(self, _c, _e):
        from shark.execution.stops import manage_stops
        # existing trail 4% < target 5% → skip
        actions = manage_stops([_pos(plpc=0.22)])
        assert actions[0]["action"] == "skipped"
        assert "already tighter" in actions[0]["reason"].lower()


# ---------------------------------------------------------------------------
# Cancel-then-place lifecycle
# ---------------------------------------------------------------------------

class TestCancelPlaceLifecycle:
    @patch(_CANCEL, return_value=True)
    @patch(_PLACE_STOP, return_value={"order_id": "new789"})
    @patch(_GET_EXISTING, return_value=(10.0, "old789"))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_cancels_old_before_placing_new(self, _c, _e, _p, _x):
        from shark.execution.stops import manage_stops
        manage_stops([_pos(plpc=0.22)])
        _x.assert_called_once_with("old789")
        _p.assert_called_once()

    @patch(_CANCEL, side_effect=RuntimeError("cancel failed"))
    @patch(_PLACE_STOP)
    @patch(_GET_EXISTING, return_value=(10.0, "old789"))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_cancel_failure_aborts_tighten(self, _c, _e, _p, _x):
        from shark.execution.stops import manage_stops
        actions = manage_stops([_pos(plpc=0.22)])
        assert actions[0]["action"] == "skipped"
        _p.assert_not_called()  # never placed new stop


# ---------------------------------------------------------------------------
# Error recovery: new stop fails after old cancelled
# ---------------------------------------------------------------------------

class TestNewStopFailure:
    @patch(_CANCEL, return_value=True)
    @patch(_PLACE_STOP, side_effect=[
        RuntimeError("new stop failed"),  # first call (new stop) fails
        {"order_id": "restored"},         # second call (restore) succeeds
    ])
    @patch(_GET_EXISTING, return_value=(10.0, "old123"))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_restores_old_stop_on_failure(self, _c, _e, _p, _x):
        from shark.execution.stops import manage_stops
        actions = manage_stops([_pos(plpc=0.22)])
        assert actions[0]["action"] == "skipped"
        # place_trailing_stop called twice: once for new, once to restore
        assert _p.call_count == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_empty_positions(self, _c):
        from shark.execution.stops import manage_stops
        assert manage_stops([]) == []

    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_zero_qty_skipped(self, _c):
        from shark.execution.stops import manage_stops
        actions = manage_stops([_pos(qty=0)])
        assert actions == []

    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_missing_symbol_skipped(self, _c):
        from shark.execution.stops import manage_stops
        actions = manage_stops([{"symbol": "", "qty": 10, "current_price": 50, "unrealized_plpc": 0.20}])
        assert actions == []

    @patch(_CANCEL, return_value=True)
    @patch(_PLACE_STOP, return_value={"order_id": "new1"})
    @patch(_GET_EXISTING, return_value=(None, None))
    @patch(_GET_CLIENT, return_value=MagicMock())
    def test_no_existing_stop_places_new(self, _c, _e, _p, _x):
        """Position has no trailing stop yet — place one."""
        from shark.execution.stops import manage_stops
        actions = manage_stops([_pos(plpc=0.22)])
        assert actions[0]["action"] == "tightened"
        _x.assert_not_called()  # nothing to cancel
