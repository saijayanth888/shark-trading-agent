"""
Order Execution — Alpaca trading API wrappers.

Handles placing, tracking, and cancelling orders. All Alpaca credentials
are read from environment variables.
"""

from __future__ import annotations
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Alpaca SDK import — deferred until first use so the module loads even
# when alpaca-trade-api is not yet installed (e.g. during pip install phase).
# ---------------------------------------------------------------------------

_tradeapi: Any = None
_APIError: Any = None


def _ensure_alpaca() -> None:
    """Import alpaca_trade_api lazily on first use."""
    global _tradeapi, _APIError
    if _tradeapi is not None:
        return
    try:
        import alpaca_trade_api as _mod  # type: ignore[import]
        from alpaca_trade_api.rest import APIError as _err  # type: ignore[import]
        _tradeapi = _mod
        _APIError = _err
    except ImportError as exc:
        raise ImportError(
            "alpaca-trade-api is not installed. Run: pip install alpaca-trade-api"
        ) from exc


def _get_client() -> Any:
    """Create an authenticated Alpaca REST client from environment variables."""
    _ensure_alpaca()
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    base_url = os.environ.get(
        "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
    )
    return _tradeapi.REST(api_key, secret_key, base_url, api_version="v2")


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

    except Exception as exc:
        logger.error("Order failed for %s: %s", symbol, exc)
        raise RuntimeError(f"Alpaca order failed for {symbol}: {exc}") from exc


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

    except Exception as exc:
        logger.error("Trailing stop failed for %s: %s", symbol, exc)
        raise RuntimeError(
            f"Alpaca trailing stop failed for {symbol}: {exc}"
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

    except Exception as exc:
        if "not found" in str(exc).lower() or "422" in str(exc):
            logger.warning("Order %s not found or already closed: %s", order_id, exc)
            return False
        logger.error("Error cancelling order %s: %s", order_id, exc)
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
            except Exception as exc:
                logger.warning(
                    "Could not cancel order %s: %s", order.id, exc
                )

        logger.info("Cancelled %d/%d open orders.", count, len(open_orders))
        return count

    except Exception as exc:
        logger.error("Error listing orders for cancellation: %s", exc)
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

    except Exception as exc:
        logger.error("Error closing position for %s: %s", symbol, exc)
        raise RuntimeError(f"Could not close position for {symbol}: {exc}") from exc


def get_open_orders(side: str | None = None) -> list[dict[str, Any]]:
    """
    Return all open (pending) orders, optionally filtered by side.

    Args:
        side: "buy", "sell", or None for all sides.

    Returns:
        List of order dicts with: order_id, symbol, side, qty, status.
    """
    api = _get_client()
    try:
        orders = api.list_orders(status="open")
        result = [_order_to_dict(o) for o in orders]
        if side:
            result = [o for o in result if o.get("side") == side]
        return result
    except Exception as exc:
        logger.error("Error fetching open orders: %s", exc)
        return []


def place_bracket_order(
    symbol: str,
    qty: int,
    trail_pct: float = 10.0,
) -> dict[str, Any]:
    """
    Place a market buy entry and immediately attach a GTC trailing stop.

    If the stop placement fails after a confirmed fill, the position is closed
    immediately to avoid an unprotected open position.

    Args:
        symbol: Ticker symbol.
        qty: Number of shares to buy.
        trail_pct: Trailing stop percentage (default 10.0%).

    Returns:
        Dict with: order_id, stop_order_id, fill_price, stop_price, symbol, qty.

    Raises:
        RuntimeError: If the entry order fails or if stop placement fails
                      and position close also fails.
    """
    # Step 1: Place market buy
    entry = place_order(symbol, qty, "buy", "market")
    order_id = entry["order_id"]
    fill_price = entry.get("filled_price")

    logger.info(
        "Bracket entry placed — %s x%d | order_id=%s | fill=$%s",
        symbol, qty, order_id, fill_price,
    )

    # Step 2: Attach trailing stop immediately
    try:
        stop = place_trailing_stop(symbol, qty, trail_percent=trail_pct)
        stop_id = stop["order_id"]
        logger.info(
            "Bracket stop attached — %s | stop_order_id=%s | trail=%.1f%%",
            symbol, stop_id, trail_pct,
        )
        return {
            "order_id": order_id,
            "stop_order_id": stop_id,
            "fill_price": fill_price,
            "stop_price": None,  # trailing stop has no fixed price
            "symbol": symbol,
            "qty": qty,
            "trail_pct": trail_pct,
        }

    except RuntimeError as stop_exc:
        # Stop failed — close position immediately rather than leave it unprotected
        logger.error(
            "Stop placement failed for %s after fill — emergency close: %s",
            symbol, stop_exc,
        )
        try:
            close_position(symbol)
            logger.warning(
                "Emergency close succeeded for %s after stop failure.", symbol
            )
        except RuntimeError as close_exc:
            logger.error(
                "Emergency close also failed for %s: %s", symbol, close_exc
            )
            raise RuntimeError(
                f"CRITICAL: {symbol} position open with no stop and close failed: {close_exc}"
            ) from close_exc

        raise RuntimeError(
            f"Stop placement failed for {symbol}; position was closed. Entry order_id={order_id}"
        ) from stop_exc
