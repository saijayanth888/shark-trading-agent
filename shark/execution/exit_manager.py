"""
Advanced Exit Manager — multi-reason exit logic beyond simple trailing stops.

Exit triggers (checked in order of priority):
  1. Hard stop: -7% from entry (unchanged — non-negotiable)
  2. Partial profit: Sell 1/3 at +1R, 1/3 at +2R, run remainder
  3. Time decay: If position stagnant for 5+ trading days, reduce/close
  4. Thesis break: Bearish sentiment shift detected by Perplexity
  5. Volatility expansion: ATR spikes 2x → tighten trailing stop aggressively
  6. Regime shift: Market regime changes to BEAR → close all within 2 days

All exits are logged with reason for post-trade review.
"""

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Hard rules
HARD_STOP_PCT = float(os.environ.get("HARD_STOP_PCT", "-0.07"))
TIME_DECAY_DAYS = int(os.environ.get("TIME_DECAY_DAYS", "5"))
TIME_DECAY_MIN_MOVE_PCT = float(os.environ.get("TIME_DECAY_MIN_MOVE_PCT", "2.0"))
VOL_EXPANSION_THRESHOLD = float(os.environ.get("VOL_EXPANSION_THRESHOLD", "2.0"))

# Partial profit tiers
TIER1_R_MULTIPLE = 1.0  # sell 1/3 at +1R
TIER2_R_MULTIPLE = 2.0  # sell 1/3 at +2R
RUNNER_TRAIL_PCT = 5.0   # tight trailing stop on remaining 1/3


def evaluate_exits(
    positions: list[dict[str, Any]],
    trade_log: list[dict[str, Any]] | None = None,
    regime: str = "BULL_QUIET",
) -> list[dict[str, Any]]:
    """
    Evaluate all open positions for exit signals.

    Args:
        positions: List of position dicts from Alpaca (must include
            symbol, qty, unrealized_plpc, current_price, avg_entry_price)
        trade_log: Historical trades for time-decay calculation
        regime: Current market regime string

    Returns:
        List of exit action dicts, each containing:
            symbol, action, reason, priority, qty_to_close, urgency
    """
    actions: list[dict[str, Any]] = []

    for pos in positions:
        symbol = pos.get("symbol", "UNKNOWN")
        qty = int(pos.get("qty", 0))
        plpc = float(pos.get("unrealized_plpc", 0.0))
        current_price = float(pos.get("current_price", 0.0))
        avg_entry = float(pos.get("avg_entry_price", current_price))

        if qty <= 0:
            continue

        pos_actions = []

        # --- EXIT 1: HARD STOP (highest priority) ---
        if plpc <= HARD_STOP_PCT:
            pos_actions.append({
                "symbol": symbol,
                "action": "CLOSE_ALL",
                "reason": f"Hard stop triggered: {plpc:.1%} <= {HARD_STOP_PCT:.0%}",
                "priority": 1,
                "qty_to_close": qty,
                "urgency": "IMMEDIATE",
            })

        # --- EXIT 2: PARTIAL PROFIT-TAKING ---
        if avg_entry > 0 and current_price > avg_entry:
            risk_per_share = avg_entry * abs(HARD_STOP_PCT)  # use hard stop as risk unit
            current_r = (current_price - avg_entry) / risk_per_share if risk_per_share > 0 else 0

            if current_r >= TIER1_R_MULTIPLE and qty >= 3:
                tier1_qty = qty // 3
                if tier1_qty > 0:
                    pos_actions.append({
                        "symbol": symbol,
                        "action": "PARTIAL_SELL",
                        "reason": f"Tier 1 profit: +{current_r:.1f}R — sell 1/3 to lock breakeven",
                        "priority": 3,
                        "qty_to_close": tier1_qty,
                        "urgency": "NEXT_CHECK",
                        "r_multiple": round(current_r, 2),
                        "tier": 1,
                    })

            if current_r >= TIER2_R_MULTIPLE and qty >= 3:
                tier2_qty = qty // 3
                if tier2_qty > 0:
                    pos_actions.append({
                        "symbol": symbol,
                        "action": "PARTIAL_SELL",
                        "reason": f"Tier 2 profit: +{current_r:.1f}R — take solid profit",
                        "priority": 3,
                        "qty_to_close": tier2_qty,
                        "urgency": "NEXT_CHECK",
                        "r_multiple": round(current_r, 2),
                        "tier": 2,
                    })

        # --- EXIT 3: TIME DECAY ---
        entry_date = _get_entry_date(symbol, trade_log)
        if entry_date:
            days_held = (date.today() - entry_date).days
            abs_move = abs(plpc * 100)

            if days_held >= TIME_DECAY_DAYS and abs_move < TIME_DECAY_MIN_MOVE_PCT:
                pos_actions.append({
                    "symbol": symbol,
                    "action": "CLOSE_ALL",
                    "reason": (
                        f"Time decay: held {days_held}d with only {plpc:.1%} move. "
                        f"Thesis expired — capital better deployed elsewhere"
                    ),
                    "priority": 4,
                    "qty_to_close": qty,
                    "urgency": "END_OF_DAY",
                    "days_held": days_held,
                })

        # --- EXIT 4: REGIME SHIFT TO BEAR ---
        if regime in ("BEAR_QUIET", "BEAR_VOLATILE"):
            pos_actions.append({
                "symbol": symbol,
                "action": "CLOSE_ALL",
                "reason": f"Regime shift to {regime} — closing longs",
                "priority": 2,
                "qty_to_close": qty,
                "urgency": "END_OF_DAY",
            })

        # Add the highest-priority action for this position
        if pos_actions:
            pos_actions.sort(key=lambda x: x["priority"])
            actions.append(pos_actions[0])

    # Sort all actions by priority (1=highest)
    actions.sort(key=lambda x: x["priority"])

    if actions:
        logger.info(
            "Exit manager: %d actions — %s",
            len(actions),
            [(a["symbol"], a["action"], a["reason"][:50]) for a in actions],
        )

    return actions


def check_volatility_expansion(
    symbol: str,
    current_atr: float,
    entry_atr: float,
) -> dict[str, Any] | None:
    """
    Check if ATR has expanded significantly since entry.

    If current ATR > entry ATR × threshold, the market is getting
    choppy and the stop should be tightened aggressively.

    Returns:
        Exit action dict if vol expansion detected, None otherwise.
    """
    if entry_atr <= 0 or current_atr <= 0:
        return None

    expansion_ratio = current_atr / entry_atr

    if expansion_ratio >= VOL_EXPANSION_THRESHOLD:
        logger.warning(
            "Volatility expansion for %s: ATR %.2f → %.2f (%.1fx)",
            symbol, entry_atr, current_atr, expansion_ratio,
        )
        return {
            "symbol": symbol,
            "action": "TIGHTEN_STOP",
            "reason": (
                f"Volatility expansion {expansion_ratio:.1f}x — "
                f"ATR {entry_atr:.2f} → {current_atr:.2f}. Tighten to 5% trail."
            ),
            "new_trail_pct": RUNNER_TRAIL_PCT,
            "priority": 2,
            "urgency": "IMMEDIATE",
        }

    return None


def compute_dynamic_stop(
    entry_price: float,
    current_price: float,
    atr: float,
    profit_pct: float,
    regime: str = "BULL_QUIET",
) -> dict[str, float]:
    """
    Compute regime-aware dynamic stop price.

    In BULL_QUIET: standard trailing (10% → 7% → 5%)
    In BULL_VOLATILE: tighter stops (8% → 5% → 3%)
    In BEAR modes: aggressive (5% → 3%)

    Args:
        entry_price: Original entry price
        current_price: Current market price
        atr: Current ATR
        profit_pct: Current unrealized profit %
        regime: Current market regime

    Returns:
        Dict with stop_price, trail_pct, method
    """
    if regime in ("BEAR_QUIET", "BEAR_VOLATILE"):
        # Aggressive trailing in bear regimes
        if profit_pct >= 10:
            trail_pct = 3.0
        else:
            trail_pct = 5.0
        method = "bear_aggressive"
    elif regime == "BULL_VOLATILE":
        # Tighter in volatile bull
        if profit_pct >= 20:
            trail_pct = 3.0
        elif profit_pct >= 10:
            trail_pct = 5.0
        else:
            trail_pct = 8.0
        method = "bull_volatile"
    else:
        # Standard bull quiet
        if profit_pct >= 20:
            trail_pct = 5.0
        elif profit_pct >= 15:
            trail_pct = 7.0
        else:
            trail_pct = 10.0
        method = "bull_quiet_standard"

    # ATR-based stop as alternative (2× ATR below current price)
    atr_stop = current_price - (2 * atr) if atr > 0 else current_price * (1 - trail_pct / 100)
    pct_stop = current_price * (1 - trail_pct / 100)

    # Use the higher (tighter) of the two
    stop_price = max(atr_stop, pct_stop)

    # Never move stop below entry once profitable
    if profit_pct > 0:
        stop_price = max(stop_price, entry_price)

    return {
        "stop_price": round(stop_price, 2),
        "trail_pct": trail_pct,
        "method": method,
    }


def _get_entry_date(symbol: str, trade_log: list[dict[str, Any]] | None) -> date | None:
    """Find the most recent entry date for a symbol from the trade log."""
    if not trade_log:
        return None

    for trade in reversed(trade_log):
        if trade.get("symbol", "").upper() == symbol.upper():
            side = trade.get("side", "").upper()
            if "BUY" in side or side == "buy":
                date_str = trade.get("date", "")
                try:
                    return date.fromisoformat(date_str)
                except (ValueError, TypeError):
                    continue

    return None
