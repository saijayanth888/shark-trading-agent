"""
Signal Generator — converts internal agent decisions into subscriber-friendly signals.

Only generates signals for high-conviction BUY decisions (confidence >= 0.70).
Returns None for NO_TRADE, WAIT, or low-confidence outcomes.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.70


def generate_signal(
    decision: dict[str, Any],
    market_data: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Convert an agent decision dict into a subscriber-ready signal.

    Args:
        decision: Output from decision_arbiter.make_decision(). Expected keys:
            decision, symbol, confidence, entry_price, stop_loss, target_price,
            risk_reward_ratio, thesis_summary.
        market_data: Current market data for the symbol (used to enrich signal).
            Expected keys (all optional): timeframe_days.

    Returns:
        Signal dict suitable for distribution to subscribers, or None if the
        decision does not meet the criteria for a published signal.
    """
    action = decision.get("decision", "NO_TRADE")
    confidence = float(decision.get("confidence", 0.0))
    symbol = decision.get("symbol", "UNKNOWN")

    # Only publish BUY signals with sufficient confidence
    if action != "BUY":
        logger.debug(
            "Signal suppressed for %s — decision is %s, not BUY.", symbol, action
        )
        return None

    if confidence < _MIN_CONFIDENCE:
        logger.debug(
            "Signal suppressed for %s — confidence %.2f below %.2f threshold.",
            symbol,
            confidence,
            _MIN_CONFIDENCE,
        )
        return None

    entry_price = float(decision.get("entry_price", 0.0))
    stop_price = float(decision.get("stop_loss", 0.0))
    target_price = float(decision.get("target_price", 0.0))
    risk_reward = float(decision.get("risk_reward_ratio", 0.0))
    thesis_summary = decision.get("thesis_summary", "")

    # Derive a human-friendly timeframe from market_data or bull thesis if available
    timeframe_days: int = int(market_data.get("timeframe_days", 0))
    if timeframe_days > 0:
        if timeframe_days <= 3:
            timeframe_str = "1-3 days"
        elif timeframe_days <= 7:
            timeframe_str = "3-7 days"
        elif timeframe_days <= 14:
            timeframe_str = "1-2 weeks"
        else:
            timeframe_str = f"{timeframe_days} days"
    else:
        timeframe_str = "2-5 days"  # sensible default

    signal: dict[str, Any] = {
        "signal_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "action": "BUY",
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "target_price": round(target_price, 2),
        "risk_reward": round(risk_reward, 2),
        "thesis_summary": thesis_summary,
        "confidence_pct": int(round(confidence * 100)),
        "timeframe": timeframe_str,
        "status": "ACTIVE",
    }

    logger.info(
        "Signal generated — %s | entry=%.2f | stop=%.2f | target=%.2f | R:R=%.1f | conf=%d%%",
        symbol,
        entry_price,
        stop_price,
        target_price,
        risk_reward,
        signal["confidence_pct"],
    )

    return signal
