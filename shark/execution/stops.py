"""
Stop Management — dynamically tightens trailing stops as positions profit.

Reads open trailing-stop orders from Alpaca and upgrades them when a position
has appreciated enough to warrant a tighter trail, protecting more profit.
Never widens or lowers an existing stop.
"""

from __future__ import annotations
import logging
from typing import Any

from shark.execution.orders import _get_client, place_trailing_stop, cancel_order

logger = logging.getLogger(__name__)

# Profit thresholds that trigger stop tightening
_TIER_20 = 0.20   # >= 20% unrealized gain → 5% trailing stop
_TIER_15 = 0.15   # >= 15% unrealized gain → 7% trailing stop
_DEFAULT_TRAIL = 10.0   # Default trailing stop %

# Never tighten closer than this percentage to current price
_MIN_TRAIL_PCT = 3.0


def _get_existing_trailing_stop(
    api: Any, symbol: str,
) -> tuple[float | None, str | None]:
    """
    Find the current trailing stop for an open GTC trailing-stop order.

    Returns (trail_percent, order_id) or (None, None) if no trailing stop exists.
    """
    try:
        from alpaca.trading.requests import GetOrdersRequest  # type: ignore[import]
        from alpaca.trading.enums import QueryOrderStatus  # type: ignore[import]

        orders = api.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
        )
        for order in orders:
            order_type = getattr(order, "type", None)
            order_side = getattr(order, "side", None)
            type_val = order_type.value if hasattr(order_type, "value") else str(order_type or "")
            side_val = order_side.value if hasattr(order_side, "value") else str(order_side or "")
            if (
                order.symbol == symbol
                and type_val == "trailing_stop"
                and side_val == "sell"
            ):
                trail_pct = getattr(order, "trail_percent", None)
                order_id = str(getattr(order, "id", "") or "")
                if trail_pct is not None:
                    return float(trail_pct), order_id
    except Exception as exc:
        logger.warning("Could not fetch orders for %s stop check: %s", symbol, exc)
    return None, None


def manage_stops(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Review all open positions and tighten trailing stops where warranted.

    Decision logic (applied per position):
    - unrealized_plpc >= 0.20 → tighten to 5% trail
    - unrealized_plpc >= 0.15 → tighten to 7% trail
    - otherwise             → 10% trail (no change required)

    Safety guardrails:
    - Never tighten to within 3% of current price (avoids premature stop-outs).
    - Never move a stop DOWN (only tighten, never loosen).
    - Skip if existing stop is already tighter than the target.

    Args:
        positions: List of position dicts from alpaca_data.get_positions().
            Expected keys: symbol, qty, current_price, unrealized_plpc.

    Returns:
        List of action dicts with keys:
            symbol, action ("tightened" | "skipped"), old_trail_pct,
            new_trail_pct, reason.
    """
    api = _get_client()
    actions: list[dict[str, Any]] = []

    for position in positions:
        symbol: str = position.get("symbol", "")
        qty: int = int(position.get("qty", 0))
        current_price: float = float(position.get("current_price", 0))
        unrealized_plpc: float = float(position.get("unrealized_plpc", 0))

        if not symbol or qty <= 0 or current_price <= 0:
            logger.warning("Skipping invalid position entry: %s", position)
            continue

        # Determine target trail percentage based on profit tier
        if unrealized_plpc >= _TIER_20:
            target_trail_pct = 5.0
            reason = f"Unrealized gain {unrealized_plpc:.1%} >= 20%; tightening to 5%."
        elif unrealized_plpc >= _TIER_15:
            target_trail_pct = 7.0
            reason = f"Unrealized gain {unrealized_plpc:.1%} >= 15%; tightening to 7%."
        else:
            # Default — no tightening warranted
            current_trail, _ = _get_existing_trailing_stop(api, symbol)
            current_trail = current_trail or _DEFAULT_TRAIL
            actions.append({
                "symbol": symbol,
                "action": "skipped",
                "old_trail_pct": current_trail,
                "new_trail_pct": current_trail,
                "reason": (
                    f"Unrealized gain {unrealized_plpc:.1%} below 15%; "
                    "default 10% trail maintained."
                ),
            })
            continue

        # Safety check: never tighten so much that the stop is within 3% of price
        min_allowed_trail = _MIN_TRAIL_PCT
        if target_trail_pct < min_allowed_trail:
            existing_trail, _ = _get_existing_trailing_stop(api, symbol)
            actions.append({
                "symbol": symbol,
                "action": "skipped",
                "old_trail_pct": existing_trail or _DEFAULT_TRAIL,
                "new_trail_pct": target_trail_pct,
                "reason": (
                    f"Target trail {target_trail_pct}% is within the "
                    f"{_MIN_TRAIL_PCT}% guardrail; skipping."
                ),
            })
            continue

        # Check the current existing trailing stop
        existing_trail, existing_order_id = _get_existing_trailing_stop(api, symbol)
        old_trail_pct = existing_trail if existing_trail is not None else _DEFAULT_TRAIL

        # Never move stop down (only tighten = smaller %)
        if existing_trail is not None and existing_trail <= target_trail_pct:
            actions.append({
                "symbol": symbol,
                "action": "skipped",
                "old_trail_pct": old_trail_pct,
                "new_trail_pct": target_trail_pct,
                "reason": (
                    f"Existing trail {existing_trail}% is already tighter "
                    f"than target {target_trail_pct}%; skipping."
                ),
            })
            continue

        # Cancel old trailing stop FIRST to avoid duplicate sell orders
        if existing_order_id:
            try:
                cancel_order(existing_order_id)
                logger.info(
                    "Cancelled old trailing stop for %s (order_id=%s, trail=%.1f%%)",
                    symbol, existing_order_id, old_trail_pct,
                )
            except Exception as exc:
                logger.error(
                    "Failed to cancel old stop for %s (order_id=%s): %s — "
                    "aborting tighten to avoid duplicate stops",
                    symbol, existing_order_id, exc,
                )
                actions.append({
                    "symbol": symbol,
                    "action": "skipped",
                    "old_trail_pct": old_trail_pct,
                    "new_trail_pct": target_trail_pct,
                    "reason": f"Could not cancel old stop: {exc}",
                })
                continue

        # Place the updated trailing stop
        try:
            place_trailing_stop(symbol, qty, trail_percent=target_trail_pct)
            logger.info(
                "Tightened trailing stop for %s: %.1f%% → %.1f%%",
                symbol,
                old_trail_pct,
                target_trail_pct,
            )
            actions.append({
                "symbol": symbol,
                "action": "tightened",
                "old_trail_pct": old_trail_pct,
                "new_trail_pct": target_trail_pct,
                "reason": reason,
            })

        except RuntimeError as exc:
            logger.error(
                "CRITICAL: Old stop cancelled but new stop failed for %s: %s — "
                "position may be unprotected!",
                symbol, exc,
            )
            # Try to restore the old stop as a safety net
            try:
                place_trailing_stop(symbol, qty, trail_percent=old_trail_pct)
                logger.info("Restored old trailing stop for %s at %.1f%%", symbol, old_trail_pct)
            except Exception as restore_exc:
                logger.error(
                    "CRITICAL: Could not restore stop for %s: %s — POSITION UNPROTECTED",
                    symbol, restore_exc,
                )
            actions.append({
                "symbol": symbol,
                "action": "skipped",
                "old_trail_pct": old_trail_pct,
                "new_trail_pct": target_trail_pct,
                "reason": f"Failed to place updated trailing stop: {exc}",
            })

    return actions
