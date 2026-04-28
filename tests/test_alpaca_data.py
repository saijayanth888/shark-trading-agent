"""Tests for shark.data.alpaca_data — M14 defensive Alpaca response parsing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from shark.data.alpaca_data import _safe_float, _safe_int


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_normal_float(self) -> None:
        assert _safe_float(3.14) == 3.14

    def test_string_float(self) -> None:
        assert _safe_float("42.5") == 42.5

    def test_int_input(self) -> None:
        assert _safe_float(10) == 10.0

    def test_none_returns_default(self) -> None:
        assert _safe_float(None) == 0.0

    def test_empty_string_returns_default(self) -> None:
        assert _safe_float("") == 0.0

    def test_garbage_returns_default(self) -> None:
        assert _safe_float("not-a-number") == 0.0

    def test_custom_default(self) -> None:
        assert _safe_float(None, default=-1.0) == -1.0


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_normal_int(self) -> None:
        assert _safe_int(5) == 5

    def test_string_int(self) -> None:
        assert _safe_int("7") == 7

    def test_float_input_truncates(self) -> None:
        assert _safe_int(3.9) == 3

    def test_string_float_truncates(self) -> None:
        assert _safe_int("3.9") == 3

    def test_none_returns_default(self) -> None:
        assert _safe_int(None) == 0

    def test_empty_string_returns_default(self) -> None:
        assert _safe_int("") == 0

    def test_garbage_returns_default(self) -> None:
        assert _safe_int("xyz") == 0

    def test_custom_default(self) -> None:
        assert _safe_int(None, default=-1) == -1


# ---------------------------------------------------------------------------
# get_account validation
# ---------------------------------------------------------------------------


class TestGetAccountValidation:
    """Verify portfolio_value <= 0 raises RuntimeError."""

    @patch("shark.data.alpaca_data._get_trading_client")
    def test_zero_portfolio_value_raises(self, mock_client_fn: MagicMock) -> None:
        from shark.data.alpaca_data import get_account

        acct = SimpleNamespace(
            equity="0",
            cash="0",
            buying_power="0",
            portfolio_value="0",
            daytrade_count="0",
        )
        mock_client_fn.return_value.get_account.return_value = acct
        with pytest.raises(RuntimeError, match="non-positive portfolio_value"):
            get_account()

    @patch("shark.data.alpaca_data._get_trading_client")
    def test_null_fields_coerced_safely(self, mock_client_fn: MagicMock) -> None:
        from shark.data.alpaca_data import get_account

        acct = SimpleNamespace(
            equity=None,
            cash=None,
            buying_power=None,
            portfolio_value="50000",
            daytrade_count=None,
        )
        mock_client_fn.return_value.get_account.return_value = acct
        result = get_account()
        assert result["equity"] == 0.0
        assert result["cash"] == 0.0
        assert result["portfolio_value"] == 50000.0
        assert result["daytrade_count"] == 0


# ---------------------------------------------------------------------------
# get_positions safe coercion
# ---------------------------------------------------------------------------


class TestGetPositionsSafeCoercion:
    """Verify positions with missing attrs don't crash."""

    @patch("shark.data.alpaca_data._get_trading_client")
    def test_missing_attrs_default_safely(self, mock_client_fn: MagicMock) -> None:
        from shark.data.alpaca_data import get_positions

        # Position with some attrs missing entirely
        pos = SimpleNamespace(symbol="AAPL", side="long")
        mock_client_fn.return_value.get_all_positions.return_value = [pos]
        result = get_positions()
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["qty"] == 0.0
        assert result[0]["avg_entry_price"] == 0.0
