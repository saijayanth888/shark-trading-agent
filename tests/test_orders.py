"""Tests for shark.execution.orders — H12 deterministic client_order_id & M14 validation."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from shark.execution.orders import (
    OrderResponseError,
    _make_client_order_id,
    _order_to_dict,
    _validate_order_response,
)


# ---------------------------------------------------------------------------
# _make_client_order_id
# ---------------------------------------------------------------------------


class TestMakeClientOrderId:
    """H12 — deterministic, UUID-formatted, collision-resistant order ids."""

    def test_returns_valid_uuid(self) -> None:
        cid = _make_client_order_id("AAPL", "buy", 10)
        # Should be parseable as a UUID
        parsed = uuid.UUID(cid)
        assert str(parsed) == cid

    def test_deterministic_same_inputs(self) -> None:
        a = _make_client_order_id("AAPL", "buy", 10, "market")
        b = _make_client_order_id("AAPL", "buy", 10, "market")
        assert a == b

    def test_different_symbol(self) -> None:
        a = _make_client_order_id("AAPL", "buy", 10)
        b = _make_client_order_id("MSFT", "buy", 10)
        assert a != b

    def test_different_side(self) -> None:
        a = _make_client_order_id("AAPL", "buy", 10)
        b = _make_client_order_id("AAPL", "sell", 10)
        assert a != b

    def test_different_qty(self) -> None:
        a = _make_client_order_id("AAPL", "buy", 10)
        b = _make_client_order_id("AAPL", "buy", 20)
        assert a != b

    def test_different_order_tag(self) -> None:
        a = _make_client_order_id("AAPL", "buy", 10, "market")
        b = _make_client_order_id("AAPL", "buy", 10, "bracket")
        assert a != b

    def test_extra_differentiates(self) -> None:
        a = _make_client_order_id("AAPL", "sell", 10, "trailing_stop", extra="trail_10.0")
        b = _make_client_order_id("AAPL", "sell", 10, "trailing_stop", extra="trail_8.0")
        assert a != b

    @patch("shark.execution.orders.date")
    def test_different_date_changes_id(self, mock_date: MagicMock) -> None:
        mock_date.today.return_value = date(2025, 1, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        a = _make_client_order_id("AAPL", "buy", 10)

        mock_date.today.return_value = date(2025, 1, 2)
        b = _make_client_order_id("AAPL", "buy", 10)
        assert a != b


# ---------------------------------------------------------------------------
# _validate_order_response
# ---------------------------------------------------------------------------


class TestValidateOrderResponse:
    """M14 — order response sanity checks."""

    def test_valid_response_passes(self) -> None:
        result = {"order_id": "abc-123", "symbol": "AAPL"}
        _validate_order_response(result, expected_symbol="AAPL")  # no raise

    def test_missing_order_id_raises(self) -> None:
        result: dict[str, Any] = {"order_id": "", "symbol": "AAPL"}
        with pytest.raises(OrderResponseError, match="no id"):
            _validate_order_response(result, expected_symbol="AAPL")

    def test_none_order_id_raises(self) -> None:
        result: dict[str, Any] = {"order_id": "None", "symbol": "AAPL"}
        with pytest.raises(OrderResponseError, match="no id"):
            _validate_order_response(result, expected_symbol="AAPL")

    def test_symbol_mismatch_raises(self) -> None:
        result = {"order_id": "abc-123", "symbol": "MSFT"}
        with pytest.raises(OrderResponseError, match="mismatch"):
            _validate_order_response(result, expected_symbol="AAPL")

    def test_case_insensitive_symbol(self) -> None:
        result = {"order_id": "abc-123", "symbol": "aapl"}
        _validate_order_response(result, expected_symbol="AAPL")  # no raise

    def test_none_symbol_passes(self) -> None:
        """When Alpaca omits symbol we can't validate — let it through."""
        result: dict[str, Any] = {"order_id": "abc-123", "symbol": None}
        _validate_order_response(result, expected_symbol="AAPL")  # no raise


# ---------------------------------------------------------------------------
# _order_to_dict
# ---------------------------------------------------------------------------


class TestOrderToDict:
    """Verify normalization captures client_order_id."""

    def _fake_order(self, **overrides: Any) -> SimpleNamespace:
        defaults = {
            "id": "order-uuid-1",
            "client_order_id": "cid-abc",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "status": "new",
            "filled_avg_price": None,
            "submitted_at": "2025-01-01T10:00:00Z",
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_includes_client_order_id(self) -> None:
        d = _order_to_dict(self._fake_order())
        assert d["client_order_id"] == "cid-abc"

    def test_missing_client_order_id_defaults_empty(self) -> None:
        order = self._fake_order()
        del order.client_order_id
        d = _order_to_dict(order)
        assert d["client_order_id"] == ""

    def test_filled_price_coercion(self) -> None:
        d = _order_to_dict(self._fake_order(filled_avg_price="123.45"))
        assert d["filled_price"] == 123.45

    def test_qty_coercion_from_string(self) -> None:
        d = _order_to_dict(self._fake_order(qty="5"))
        assert d["qty"] == 5
        assert isinstance(d["qty"], int)
