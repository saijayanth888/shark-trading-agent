"""
Order Execution — Alpaca Python SDK (alpaca-py) wrappers.

Handles placing, tracking, and cancelling orders. All Alpaca credentials
are read from environment variables.
"""

from __future__ import annotations
import functools
import hashlib
import os
import logging
import time
import uuid
from datetime import date, timezone
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Max seconds to poll for a market order fill before giving up
_FILL_POLL_TIMEOUT = 10
_FILL_POLL_INTERVAL = 0.5


# ---------------------------------------------------------------------------
# Deterministic client_order_id — idempotent orders (H12)
# ---------------------------------------------------------------------------

def _make_client_order_id(
    symbol: str,
    side: str,
    qty: int | float,
    order_tag: str = "market",
    *,
    extra: str = "",
) -> str:
    """Generate a deterministic UUID-format client_order_id.

    The id is derived from (symbol, side, qty, order_tag, today's date,
    optional extra) via SHA-256 → UUID5-style truncation. Alpaca rejects
    duplicate client_order_ids within the same day, so retrying the exact
    same logical order is safe (idempotent) without risking double-fills.

    ``order_tag`` differentiates order types ("market", "trailing_stop",
    "bracket", etc.) so a buy entry and its protective stop never collide.
    """
    today = date.today().isoformat()  # YYYY-MM-DD
    payload = f"{symbol}|{side}|{qty}|{order_tag}|{today}|{extra}"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    # Format as 36-char UUID string — Alpaca accepts up to 48 chars.
    return str(uuid.UUID(digest[:32]))


# ---------------------------------------------------------------------------
# Order response validation (M14)
# ---------------------------------------------------------------------------

class OrderResponseError(RuntimeError):
    """Raised when an Alpaca order response fails validation."""


def _validate_order_response(result: dict[str, Any], *, expected_symbol: str) -> None:
    """Sanity-check critical fields before we trust the order response.

    Raises OrderResponseError on problems so callers can decide how to
    handle rather than silently proceeding on garbage data.
    """
    oid = result.get("order_id")
    if not oid or oid == "None":
        raise OrderResponseError(
            f"Alpaca returned an order with no id for {expected_symbol}"
        )
    sym = (result.get("symbol") or "").upper()
    if sym and sym != expected_symbol.upper():
        raise OrderResponseError(
            f"Symbol mismatch: expected {expected_symbol}, got {sym} "
            f"(order_id={oid})"
        )

# ---------------------------------------------------------------------------
# Lazy Alpaca SDK client — deferred until first use so the module loads even
# when alpaca-py is not yet installed (e.g. during pip install phase).
# ---------------------------------------------------------------------------

_trading_client: Any = None


def _enum_val(v: Any) -> str:
    """Extract string value from an alpaca-py enum or passthrough."""
    return v.value if hasattr(v, "value") else str(v or "")


def _retry_order(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 15.0,
    retryable: tuple[type[Exception], ...] = (OSError, ConnectionError, TimeoutError),
) -> Callable[[F], F]:
    """Retry decorator for order operations with exponential backoff."""
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retryable as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "%s attempt %d/%d failed (%s) — retrying in %.1fs",
                        fn.__name__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
                except Exception:
                    raise
            raise RuntimeError(
                f"{fn.__name__} failed after {max_attempts} attempts: {last_exc}"
            ) from last_exc
        return wrapper  # type: ignore[return-value]
    return decorator


def _poll_for_fill(client: Any, order_id: str) -> dict[str, Any] | None:
    """Poll Alpaca for order fill status up to _FILL_POLL_TIMEOUT seconds.

    Returns the updated order object if filled, or None if still unfilled.
    """
    deadline = time.monotonic() + _FILL_POLL_TIMEOUT
    while time.monotonic() < deadline:
        try:
            order = client.get_order_by_id(order_id)
            status = _enum_val(getattr(order, "status", "")).lower()
            if status == "filled":
                return _order_to_dict(order)
            if status in ("canceled", "cancelled", "expired", "rejected"):
                logger.warning("Order %s reached terminal status: %s", order_id, status)
                return _order_to_dict(order)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Poll error for order %s: %s", order_id, exc)
        time.sleep(_FILL_POLL_INTERVAL)
    logger.warning("Order %s not filled within %ds poll window", order_id, _FILL_POLL_TIMEOUT)
    return None


def get_existing_position(symbol: str) -> dict[str, Any] | None:
    """Check if we already have an open position for symbol.

    Returns position dict or None. Used as a duplicate trade guard.
    """
    client = _get_client()
    try:
        pos = client.get_open_position(symbol)
        return {
            "symbol": getattr(pos, "symbol", symbol),
            "qty": int(float(getattr(pos, "qty", 0) or 0)),
            "avg_entry_price": float(getattr(pos, "avg_entry_price", 0) or 0),
            "market_value": float(getattr(pos, "market_value", 0) or 0),
            "unrealized_pl": float(getattr(pos, "unrealized_pl", 0) or 0),
        }
    except Exception:  # noqa: BLE001
        return None  # no position = normal case


def _get_client() -> Any:
    """Return (or lazily create) an authenticated Alpaca TradingClient."""
    global _trading_client
    if _trading_client is not None:
        return _trading_client

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    base_url = os.environ.get(
        "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
    )

    if not api_key or not secret_key:
        raise EnvironmentError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set."
        )

    try:
        from alpaca.trading.client import TradingClient  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "alpaca-py is not installed. Run: pip install alpaca-py"
        ) from exc

    paper = "paper" in base_url.lower()
    _trading_client = TradingClient(
        api_key=api_key,
        secret_key=secret_key,
        paper=paper,
    )
    return _trading_client


# ---------------------------------------------------------------------------
# Order helpers
# ---------------------------------------------------------------------------

def _order_to_dict(order: Any) -> dict[str, Any]:
    """Normalize an Alpaca order object to a plain dict."""
    return {
        "order_id": str(getattr(order, "id", "") or ""),
        "client_order_id": str(getattr(order, "client_order_id", "") or ""),
        "symbol": getattr(order, "symbol", None),
        "side": _enum_val(getattr(order, "side", "")),
        "qty": int(float(getattr(order, "qty", 0) or 0)),
        "status": _enum_val(getattr(order, "status", "")),
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

@_retry_order(max_attempts=3, base_delay=1.0)
def place_order(
    symbol: str,
    qty: int,
    side: str,
    order_type: str = "market",
) -> dict[str, Any]:
    """
    Place an equity order on Alpaca.

    For market orders, polls for fill confirmation up to _FILL_POLL_TIMEOUT
    seconds so the returned dict always has an accurate filled_price.

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
    client = _get_client()

    try:
        from alpaca.trading.requests import MarketOrderRequest  # type: ignore[import]
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore[import]

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        cid = _make_client_order_id(symbol, side, qty, order_tag=order_type)

        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            client_order_id=cid,
        )
        order = client.submit_order(order_data=order_data)
        result = _order_to_dict(order)
        _validate_order_response(result, expected_symbol=symbol)
        logger.info(
            "Order placed — %s %s x%d | id=%s | status=%s",
            side.upper(),
            symbol,
            qty,
            result["order_id"],
            result["status"],
        )

        # Poll for fill if market order wasn't immediately filled
        if order_type == "market" and result.get("filled_price") is None:
            filled = _poll_for_fill(client, result["order_id"])
            if filled:
                result = filled
                logger.info(
                    "Order filled — %s %s x%d | fill=$%s",
                    side.upper(), symbol, qty, result["filled_price"],
                )

        return result

    except OrderResponseError:
        raise
    except Exception as exc:
        logger.error("Order failed for %s: %s", symbol, exc)
        raise RuntimeError(f"Alpaca order failed for {symbol}: {exc}") from exc


@_retry_order(max_attempts=3, base_delay=1.0)
def place_trailing_stop(
    symbol: str,
    qty: int,
    trail_percent: float = 10.0,
) -> dict[str, Any]:
    """
    Place a trailing-stop sell order (Good-Till-Cancelled).

    Args:
        symbol: Ticker symbol.
        qty: Number of shares to protect.
        trail_percent: Trailing stop percentage (default 10.0%).

    Returns:
        Dict with order_id, symbol, side, qty, status, filled_price, submitted_at.

    Raises:
        RuntimeError: If the Alpaca API returns an error.
    """
    client = _get_client()

    try:
        from alpaca.trading.requests import TrailingStopOrderRequest  # type: ignore[import]
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore[import]

        cid = _make_client_order_id(
            symbol, "sell", qty, order_tag="trailing_stop",
            extra=f"trail_{trail_percent}",
        )

        order_data = TrailingStopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            trail_percent=trail_percent,
            client_order_id=cid,
        )
        order = client.submit_order(order_data=order_data)
        result = _order_to_dict(order)
        _validate_order_response(result, expected_symbol=symbol)
        logger.info(
            "Trailing stop placed — %s x%d @ %.1f%% trail | id=%s | status=%s",
            symbol,
            qty,
            trail_percent,
            result["order_id"],
            result["status"],
        )
        return result

    except OrderResponseError:
        raise
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
    client = _get_client()

    try:
        client.cancel_order_by_id(order_id)
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
    client = _get_client()

    try:
        from alpaca.trading.requests import GetOrdersRequest  # type: ignore[import]
        from alpaca.trading.enums import QueryOrderStatus  # type: ignore[import]

        open_orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        if not open_orders:
            logger.info("No open orders to cancel.")
            return 0

        count = 0
        for order in open_orders:
            try:
                client.cancel_order_by_id(str(order.id))
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


@_retry_order(max_attempts=3, base_delay=1.0)
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
    client = _get_client()

    try:
        # Get current position to compute realized P&L
        position = client.get_open_position(symbol)
        qty = int(float(getattr(position, "qty", 0) or 0))
        cost_basis = float(getattr(position, "cost_basis", 0) or 0)
        market_value = float(getattr(position, "market_value", 0) or 0)
        realized_pl = market_value - cost_basis

        # Use the SDK's native close_position for reliability
        order = client.close_position(symbol)

        result = _order_to_dict(order)
        _validate_order_response(result, expected_symbol=symbol)
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


@_retry_order(max_attempts=2, base_delay=0.5)
def get_open_orders(side: str | None = None) -> list[dict[str, Any]]:
    """
    Return all open (pending) orders, optionally filtered by side.

    Args:
        side: "buy", "sell", or None for all sides.

    Returns:
        List of order dicts with: order_id, symbol, side, qty, status.
    """
    client = _get_client()
    try:
        from alpaca.trading.requests import GetOrdersRequest  # type: ignore[import]
        from alpaca.trading.enums import QueryOrderStatus  # type: ignore[import]

        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        result = [_order_to_dict(o) for o in orders]
        if side:
            result = [o for o in result if o.get("side") == side]
        return result
    except Exception as exc:
        logger.error("Error fetching open orders: %s", exc)
        return []


@_retry_order(max_attempts=3, base_delay=1.0)
def _place_true_bracket(
    symbol: str,
    qty: int,
    stop_loss: float,
    take_profit: float,
) -> dict[str, Any]:
    """Submit a single Alpaca BRACKET parent with stop+target legs (OCO).

    Alpaca attaches the stop-loss + take-profit limit children atomically
    once the parent market order fills. This is the preferred path for
    LLM-driven entries because the broker enforces the exact stop and target
    the analyst computed (no slippage between thesis and execution).
    """
    client = _get_client()
    try:
        from alpaca.trading.requests import (  # type: ignore[import]
            MarketOrderRequest,
            TakeProfitRequest,
            StopLossRequest,
        )
        from alpaca.trading.enums import (  # type: ignore[import]
            OrderClass,
            OrderSide,
            TimeInForce,
        )

        cid = _make_client_order_id(
            symbol, "buy", qty, order_tag="bracket",
            extra=f"sl_{stop_loss}_tp_{take_profit}",
        )

        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
            client_order_id=cid,
        )
        order = client.submit_order(order_data=order_data)
        result = _order_to_dict(order)
        _validate_order_response(result, expected_symbol=symbol)
        order_id = result["order_id"]

        logger.info(
            "Bracket parent submitted — %s x%d | id=%s | stop=$%.2f target=$%.2f",
            symbol, qty, order_id, stop_loss, take_profit,
        )

        # Poll for parent fill so we can return an accurate fill price
        if result.get("filled_price") is None:
            filled = _poll_for_fill(client, order_id)
            if filled:
                result = filled

        # Validate fill status
        status = (result.get("status") or "").lower()
        if status in ("canceled", "cancelled", "expired", "rejected"):
            raise RuntimeError(
                f"Bracket parent for {symbol} was {status} (order_id={order_id})"
            )

        return {
            "order_id": order_id,
            "stop_order_id": None,  # children are managed by Alpaca, no separate id surfaced
            "fill_price": result.get("filled_price"),
            "stop_price": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "symbol": symbol,
            "qty": qty,
            "order_class": "bracket",
        }
    except (RuntimeError, OrderResponseError):
        raise
    except Exception as exc:
        logger.error("Bracket order failed for %s: %s", symbol, exc)
        raise RuntimeError(f"Alpaca bracket order failed for {symbol}: {exc}") from exc


def place_bracket_order(
    symbol: str,
    qty: int,
    trail_pct: float = 10.0,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> dict[str, Any]:
    """
    Place a market buy entry plus protective exits.

    Two execution paths:

    1. **True bracket** (preferred) — when both stop_loss and take_profit are
       given, submit a single Alpaca BRACKET parent. Alpaca attaches an OCO
       pair (stop-loss + take-profit limit) atomically once the parent fills.
       Both legs use GTC.

    2. **Trailing-stop fallback** — when stop_loss/take_profit are missing,
       place a market buy then attach a separate trailing-stop sell.
       The position has no profit target.

    Includes duplicate trade guard — will not open a second position if one
    already exists. If protection placement fails after a confirmed fill, the
    position is closed immediately to avoid an unprotected open position.

    Args:
        symbol: Ticker symbol.
        qty: Number of shares to buy.
        trail_pct: Trailing stop percentage (used only on the fallback path).
        stop_loss: Hard stop price for the bracket child (optional).
        take_profit: Limit price for the take-profit child (optional).

    Returns:
        Dict with: order_id, stop_order_id, fill_price, stop_price, symbol, qty,
        plus take_profit when bracket mode is used.

    Raises:
        RuntimeError: If the entry order fails or if stop placement fails
                      and position close also fails.
    """
    # Duplicate trade guard — check for existing position
    existing = get_existing_position(symbol)
    if existing:
        logger.warning(
            "DUPLICATE GUARD: Already holding %s x%d — skipping bracket order",
            symbol, existing["qty"],
        )
        raise RuntimeError(
            f"Duplicate trade blocked: already holding {symbol} x{existing['qty']}"
        )

    # Path 1 — true Alpaca BRACKET when LLM provided both legs
    if stop_loss is not None and take_profit is not None:
        return _place_true_bracket(symbol, qty, float(stop_loss), float(take_profit))

    # Path 2 — legacy trailing-stop fallback
    # Step 1: Place market buy (with fill polling)
    entry = place_order(symbol, qty, "buy", "market")
    order_id = entry["order_id"]
    fill_price = entry.get("filled_price")

    # Validate fill status before attaching stop
    entry_status = (entry.get("status") or "").lower()
    if entry_status in ("canceled", "cancelled", "expired", "rejected"):
        raise RuntimeError(
            f"Entry order for {symbol} was {entry_status} (order_id={order_id})"
        )

    logger.info(
        "Bracket entry placed — %s x%d | order_id=%s | fill=$%s | status=%s",
        symbol, qty, order_id, fill_price, entry_status,
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
