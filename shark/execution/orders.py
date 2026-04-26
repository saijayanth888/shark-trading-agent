"""
Order Execution — Alpaca trading API wrappers.

Handles placing, tracking, and cancelling orders. All Alpaca credentials
are read from environment variables.
"""

import os
import logging
from typing import Any

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alpaca client factory
# ---------------------------------------------------------------------------

def _get_client() -> tradeapi.REST:
    """Create an authenticated Alpaca REST client from environment variables."""
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    base_url = os.environ.get(
        "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
    )
    return tradeapi.REST(api_key, secret_key, base_url, api_version="v2")


# ---------------------------------------------------------------------------
# Order helpers
# ---------------------------------------------------------------------------

def _order_to_dict(order: Any) -> dict[str, Any]:
    """Normalize an Alpaca order object to a plain dict."""
    return {
        "order_id": getattr(order, "id", None),
        "symbol": getattr(order, "symbol", None),
        "side": getattr(order, "side", None),
        "qty": int(getattr(order, "qty", 0) or 0),
        "status": getattr(order, "status", None),
        "filled_price": (
            float(order.filled_avg_price)
            if getattr(order, "filled_avg_price", None)
            else None
        ),
        "submitted_at": str(getattr(order, "submitted_at", "")),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def place_order(
    symbol: str,
    qty: int,
    side: str,
    order_type: str = "market",
) -> dict[str, Any]:
    """
    Place an equity order on Alpaca.

    Args:
        symbol: Ticker symbol (e.g. "AAPL").
        qty: Number of shares to trade.
        side: "buy" or "sell".
        order_type: "market" (default) or "limit".

    Returns:
        Dict with order_id, symbol, side, qty, status, filled_price, submitted_at.

    Raises:
        RuntimeError: If the Alpaca API returns an error.
    """
    api = _get_client()

    try:
        order = api.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type=order_type,
            time_in_force="day",
        )
        result = _order_to_dict(order)
        logger.info(
            "Order placed — %s %s x%d | id=%s | status=%s",
            side.upper(),
            symbol,
            qty,
            result["order_id"],
            result["status"],
        )
        return result

    except APIError as exc:
        logger.error("Alpaca APIError placing order for %s: %s", symbol, exc)
        raise RuntimeError(f"Alpaca order failed for {symbol}: {exc}") from exc

    except Exception as exc:
        logger.error("Unexpected error placing order for %s: %s", symbol, exc)
        raise RuntimeError(f"Unexpected error placing order for {symbol}: {exc}") from exc


def place_trailing_stop(
    symbol: str,
    qty: int,
    trail_percent: float = 10.0,
) -> dict[str, Any]:
    """
    Place a trailing-stop sell order (Good-Till-Cancelled).

    Alpaca requires trail_percent as a string (e.g. "10.0"), not a float.

    Args:
        symbol: Ticker symbol.
        qty: Number of shares to protect.
        trail_percent: Trailing stop percentage (default 10.0%).

    Returns:
        Dict with order_id, symbol, side, qty, status, filled_price, submitted_at.

    Raises:
        RuntimeError: If the Alpaca API returns an error.
    """
    api = _get_client()

    try:
        order = api.submit_order(
            symbol=symbol,
            qty=qty,
            side="sell",
            type="trailing_stop",
            time_in_force="gtc",
            trail_percent=str(trail_percent),  # Alpaca quirk: must be string
        )
        result = _order_to_dict(order)
        logger.info(
            "Trailing stop placed — %s x%d @ %.1f%% trail | id=%s | status=%s",
            symbol,
            qty,
            trail_percent,
            result["order_id"],
            result["status"],
        )
        return result

    except APIError as exc:
        logger.error(
            "Alpaca APIError placing trailing stop for %s: %s", symbol, exc
        )
        raise RuntimeError(
            f"Alpaca trailing stop failed for {symbol}: {exc}"
        ) from exc

    except Exception as exc:
        logger.error(
            "Unexpected error placing trailing stop for %s: %s", symbol, exc
        )
        raise RuntimeError(
            f"Unexpected error placing trailing stop for {symbol}: {exc}"
        ) from exc


def cancel_order(order_id: str) -> bool:
    """
    Cancel a single open order by ID.

    Args:
        order_id: Alpaca order UUID.

    Returns:
        True if cancelled successfully, False if not found or already closed.
    """
    api = _get_client()

    try:
        api.cancel_order(order_id)
        logger.info("Order cancelled — id=%s", order_id)
        return True

    except APIError as exc:
        # 422 = order not found or not cancellable
        if "not found" in str(exc).lower() or "422" in str(exc):
            logger.warning("Order %s not found or already closed: %s", order_id, exc)
            return False
        logger.error("APIError cancelling order %s: %s", order_id, exc)
        return False

    except Exception as exc:
        logger.error("Unexpected error cancelling order %s: %s", order_id, exc)
        return False


def cancel_all_orders() -> int:
    """
    Cancel all open orders.

    Returns:
        Number of orders successfully cancelled.
    """
    api = _get_client()

    try:
        open_orders = api.list_orders(status="open")
        if not open_orders:
            logger.info("No open orders to cancel.")
            return 0

        count = 0
        for order in open_orders:
            try:
                api.cancel_order(order.id)
                count += 1
                logger.info("Cancelled order %s (%s)", order.id, order.symbol)
            except APIError as exc:
                logger.warning(
                    "Could not cancel order %s: %s", order.id, exc
                )

        logger.info("Cancelled %d/%d open orders.", count, len(open_orders))
        return count

    except APIError as exc:
        logger.error("APIError listing orders for cancellation: %s", exc)
        return 0

    except Exception as exc:
        logger.error("Unexpected error cancelling all orders: %s", exc)
        return 0


def close_position(symbol: str) -> dict[str, Any]:
    """
    Close the entire open position for a symbol via a market sell.

    Args:
        symbol: Ticker symbol.

    Returns:
        Dict with order details and realized_pl if available.

    Raises:
        RuntimeError: If the position cannot be closed.
    """
    api = _get_client()

    try:
        # Get current position to know the qty
        position = api.get_position(symbol)
        qty = int(position.qty)
        cost_basis = float(position.cost_basis or 0)
        market_value = float(position.market_value or 0)
        realized_pl = market_value - cost_basis

        order = api.submit_order(
            symbol=symbol,
            qty=qty,
            side="sell",
            type="market",
            time_in_force="day",
        )

        result = _order_to_dict(order)
        result["realized_pl"] = realized_pl
        result["qty_closed"] = qty

        logger.info(
            "Position closed — %s x%d | realized P&L approx: $%.2f | order_id=%s",
            symbol,
            qty,
            realized_pl,
            result["order_id"],
        )
        return result

    except APIError as exc:
        logger.error("APIError closing position for %s: %s", symbol, exc)
        raise RuntimeError(f"Could not close position for {symbol}: {exc}") from exc

    except Exception as exc:
        logger.error("Unexpected error closing position for %s: %s", symbol, exc)
        raise RuntimeError(
            f"Unexpected error closing position for {symbol}: {exc}"
        ) from exc
