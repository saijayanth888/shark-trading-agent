"""
Risk Manager — pure Python guardrails for trade approval.

No AI involved. Hard rule checks that must ALL pass before a trade is approved.
All limits are configurable via environment variables.
"""

from __future__ import annotations
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Defaults read at module level so they can be inspected/overridden easily
_MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "6"))
_MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))
_MAX_WEEKLY_TRADES = int(os.getenv("MAX_WEEKLY_TRADES", "3"))
_MIN_CASH_BUFFER_PCT = float(os.getenv("MIN_CASH_BUFFER_PCT", "0.15"))
_CIRCUIT_BREAKER_PCT = float(os.getenv("CIRCUIT_BREAKER_PCT", "0.15"))


def _check_max_positions(
    current_positions: list[dict], max_positions: int
) -> dict[str, Any]:
    """Verify adding one more position stays within the max-positions limit."""
    new_count = len(current_positions) + 1
    passed = new_count <= max_positions
    return {
        "passed": passed,
        "message": (
            f"OK — {new_count}/{max_positions} positions after trade."
            if passed
            else f"FAIL — adding position would reach {new_count}, limit is {max_positions}."
        ),
    }


def _check_position_size(
    estimated_cost: float, portfolio_value: float, max_position_pct: float
) -> tuple[dict[str, Any], int | None]:
    """Verify the position does not exceed max single-position percentage."""
    if portfolio_value <= 0:
        return {"passed": False, "message": "FAIL — portfolio_value is zero or negative."}, None

    actual_pct = estimated_cost / portfolio_value
    passed = actual_pct <= max_position_pct

    # If failed, calculate the adjusted qty that would fit within limit
    adjusted_size: int | None = None
    if not passed:
        max_cost = portfolio_value * max_position_pct
        # estimated_cost / qty gives price per share; adjust qty proportionally
        # We'll return max_cost as a hint; caller must convert using price
        adjusted_size = int(max_cost)  # placeholder — orders.py converts to qty

    return (
        {
            "passed": passed,
            "message": (
                f"OK — position is {actual_pct:.1%} of portfolio (limit {max_position_pct:.0%})."
                if passed
                else f"FAIL — position is {actual_pct:.1%}, exceeds limit of {max_position_pct:.0%}."
            ),
        },
        adjusted_size,
    )


def _check_weekly_trades(
    weekly_trade_count: int, max_weekly_trades: int
) -> dict[str, Any]:
    """Verify the weekly trade count stays within limits."""
    new_count = weekly_trade_count + 1
    passed = new_count <= max_weekly_trades
    return {
        "passed": passed,
        "message": (
            f"OK — {new_count}/{max_weekly_trades} trades this week."
            if passed
            else f"FAIL — would be trade #{new_count} this week, limit is {max_weekly_trades}."
        ),
    }


def _check_cash_buffer(
    cash: float,
    estimated_cost: float,
    portfolio_value: float,
    min_cash_buffer_pct: float,
) -> dict[str, Any]:
    """Verify sufficient cash buffer remains after the trade."""
    if portfolio_value <= 0:
        return {"passed": False, "message": "FAIL — portfolio_value is zero or negative."}

    cash_after = cash - estimated_cost
    buffer_pct = cash_after / portfolio_value
    passed = buffer_pct >= min_cash_buffer_pct
    return {
        "passed": passed,
        "message": (
            f"OK — cash buffer after trade: {buffer_pct:.1%} (min {min_cash_buffer_pct:.0%})."
            if passed
            else f"FAIL — cash buffer after trade would be {buffer_pct:.1%}, below minimum {min_cash_buffer_pct:.0%}."
        ),
    }


def _check_circuit_breaker(
    portfolio_value: float,
    peak_equity: float,
    circuit_breaker_pct: float,
) -> dict[str, Any]:
    """Halt trading if drawdown from peak equity exceeds circuit breaker threshold."""
    if peak_equity <= 0:
        return {"passed": False, "message": "FAIL — peak_equity is zero or negative."}

    drawdown = (peak_equity - portfolio_value) / peak_equity
    passed = drawdown < circuit_breaker_pct
    return {
        "passed": passed,
        "message": (
            f"OK — drawdown from peak is {drawdown:.1%} (limit {circuit_breaker_pct:.0%})."
            if passed
            else f"FAIL — circuit breaker triggered! Drawdown {drawdown:.1%} exceeds {circuit_breaker_pct:.0%} limit."
        ),
    }


def _check_stocks_only(proposed_trade: dict[str, Any]) -> dict[str, Any]:
    """Ensure only stock instruments are traded (no options, crypto, futures)."""
    instrument_type = proposed_trade.get("instrument_type", "stock")
    passed = instrument_type == "stock"
    return {
        "passed": passed,
        "message": (
            "OK — instrument type is stock."
            if passed
            else f"FAIL — instrument type '{instrument_type}' is not allowed; only 'stock' permitted."
        ),
    }


def check_risk(
    proposed_trade: dict[str, Any],
    current_positions: list[dict[str, Any]],
    account: dict[str, Any],
    *,
    weekly_trade_count: int = 0,
    peak_equity: float | None = None,
) -> dict[str, Any]:
    """
    Run all risk guardrail checks against a proposed trade.

    Args:
        proposed_trade: Dict with keys: symbol, side, qty, estimated_cost, sector.
            Optional key "instrument_type" (default "stock").
        current_positions: List of current open positions (from alpaca_data.get_positions()).
        account: Account info dict (from alpaca_data.get_account()) with keys:
            portfolio_value (float), cash (float).
        weekly_trade_count: Number of trades already placed this week (default 0).
        peak_equity: Historical peak portfolio value; if None, defaults to account
            portfolio_value or env var PEAK_EQUITY.

    Returns:
        Dict with keys:
            approved (bool): True only if ALL checks pass.
            violations (list[str]): Human-readable failure messages.
            adjusted_size (int): Suggested qty if size check failed, else original qty.
            checks (dict): Individual check results keyed by check name.
    """
    # Resolve limits from env (re-read each call so tests can override)
    max_positions = int(os.getenv("MAX_POSITIONS", str(_MAX_POSITIONS)))
    max_position_pct = float(os.getenv("MAX_POSITION_PCT", str(_MAX_POSITION_PCT)))
    max_weekly_trades = int(os.getenv("MAX_WEEKLY_TRADES", str(_MAX_WEEKLY_TRADES)))
    min_cash_buffer_pct = float(os.getenv("MIN_CASH_BUFFER_PCT", str(_MIN_CASH_BUFFER_PCT)))
    circuit_breaker_pct = float(os.getenv("CIRCUIT_BREAKER_PCT", str(_CIRCUIT_BREAKER_PCT)))

    portfolio_value = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    estimated_cost = float(proposed_trade.get("estimated_cost", 0))
    qty = int(proposed_trade.get("qty", 0))

    # Resolve peak equity
    if peak_equity is None:
        env_peak = os.getenv("PEAK_EQUITY")
        peak_equity = float(env_peak) if env_peak else portfolio_value

    # Run individual checks
    max_pos_result = _check_max_positions(current_positions, max_positions)

    pos_size_result, adjusted_cost = _check_position_size(
        estimated_cost, portfolio_value, max_position_pct
    )

    weekly_result = _check_weekly_trades(weekly_trade_count, max_weekly_trades)

    cash_result = _check_cash_buffer(
        cash, estimated_cost, portfolio_value, min_cash_buffer_pct
    )

    cb_result = _check_circuit_breaker(portfolio_value, peak_equity, circuit_breaker_pct)

    stocks_result = _check_stocks_only(proposed_trade)

    checks = {
        "max_positions": max_pos_result,
        "position_size": pos_size_result,
        "weekly_trades": weekly_result,
        "cash_buffer": cash_result,
        "circuit_breaker": cb_result,
        "stocks_only": stocks_result,
    }

    violations = [
        result["message"]
        for result in checks.values()
        if not result["passed"]
    ]

    approved = len(violations) == 0

    # Calculate adjusted qty if position size check failed
    if not pos_size_result["passed"] and portfolio_value > 0 and estimated_cost > 0:
        price_per_share = estimated_cost / qty if qty > 0 else 1.0
        max_affordable = portfolio_value * max_position_pct
        adjusted_qty = max(1, int(max_affordable / price_per_share))
    else:
        adjusted_qty = qty

    if approved:
        logger.info(
            "Risk check APPROVED for %s (qty=%d, cost=%.2f)",
            proposed_trade.get("symbol"),
            qty,
            estimated_cost,
        )
    else:
        logger.warning(
            "Risk check REJECTED for %s — violations: %s",
            proposed_trade.get("symbol"),
            violations,
        )

    return {
        "approved": approved,
        "violations": violations,
        "adjusted_size": adjusted_qty,
        "checks": checks,
    }
