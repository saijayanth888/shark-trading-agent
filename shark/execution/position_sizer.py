"""
Advanced Position Sizing — risk-normalized, regime-adjusted, drawdown-aware.

Three sizing methods combined:

1. ATR-based (primary): Risk the same dollar amount per trade regardless of
   stock volatility. Volatile stocks get fewer shares, quiet stocks get more.
   Formula: shares = risk_per_trade / (ATR × stop_multiple)

2. Fractional Kelly: Optimal growth rate sizing based on historical win rate
   and avg win/loss ratio. Uses ¼ Kelly to reduce variance.
   Formula: kelly = (win_rate × avg_win/avg_loss - (1 - win_rate)) / (avg_win/avg_loss)
            position = kelly × 0.25 × portfolio

3. Regime adjustment: Final size multiplied by regime factor
   BULL_QUIET=1.0, BULL_VOLATILE=0.5, BEAR=0.0

4. Drawdown scaling: Reduce size proportionally as drawdown deepens
   At -5% drawdown → 80% of normal size
   At -10% drawdown → 50% of normal size
   At -15% drawdown → circuit breaker

Result: min(ATR_size, Kelly_size, guardrail_max) × regime_mult × drawdown_mult
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Risk per trade as fraction of portfolio (0.01 = 1%).  Matches config.py convention.
_BASE_RISK_FRAC = float(os.environ.get("RISK_PER_TRADE_PCT", "0.01"))

# ATR multiplier for stop distance (2.0 = stop at 2× ATR below entry)
_ATR_STOP_MULTIPLE = float(os.environ.get("ATR_STOP_MULTIPLE", "2.0"))

# Max position size as fraction of portfolio (0.20 = 20%).  Matches config.py convention.
_MAX_POSITION_FRAC = float(os.environ.get("MAX_POSITION_PCT", "0.20"))

# Min position size in shares
_MIN_SHARES = 1

# Fractional Kelly fraction (0.25 = quarter Kelly — conservative)
_KELLY_FRACTION = float(os.environ.get("KELLY_FRACTION", "0.25"))


def compute_position_size(
    portfolio_value: float,
    current_price: float,
    atr: float,
    regime_multiplier: float = 1.0,
    peak_equity: float = 0.0,
    win_rate: float = 0.55,
    avg_win_loss_ratio: float = 2.0,
    confidence: float = 0.70,
) -> dict[str, Any]:
    """
    Compute risk-normalized position size.

    Args:
        portfolio_value: Current portfolio value
        current_price: Stock's current price
        atr: 14-day Average True Range of the stock
        regime_multiplier: From market_regime (1.0 = full, 0.5 = half, 0.0 = none)
        peak_equity: Portfolio peak for drawdown calculation
        win_rate: Historical win rate (0-1), default 0.55
        avg_win_loss_ratio: Avg win / avg loss, default 2.0
        confidence: AI confidence in this trade (0-1)

    Returns:
        Dict with:
            shares (int): Final number of shares to buy
            dollar_amount (float): Total dollar cost
            risk_dollars (float): Dollar amount at risk
            stop_price (float): ATR-based stop price
            method_used (str): Which sizing method was binding
            sizing_details (dict): Breakdown of all methods
    """
    if portfolio_value <= 0 or current_price <= 0:
        return _zero_result("invalid portfolio or price")

    if regime_multiplier <= 0:
        return _zero_result("regime blocks new trades (BEAR mode)")

    # --- METHOD 1: ATR-BASED SIZING ---
    risk_dollars = portfolio_value * _BASE_RISK_FRAC
    stop_distance = atr * _ATR_STOP_MULTIPLE if atr > 0 else current_price * 0.10

    # Prevent absurdly tight stops
    min_stop_distance = current_price * 0.02  # at least 2% stop
    stop_distance = max(stop_distance, min_stop_distance)

    atr_shares = int(risk_dollars / stop_distance) if stop_distance > 0 else 0
    atr_stop_price = round(current_price - stop_distance, 2)

    # --- METHOD 2: FRACTIONAL KELLY ---
    kelly_pct = _compute_kelly(win_rate, avg_win_loss_ratio)
    kelly_dollars = portfolio_value * kelly_pct
    kelly_shares = int(kelly_dollars / current_price) if current_price > 0 else 0

    # --- MAX CAP: Guardrail limit ---
    max_dollars = portfolio_value * _MAX_POSITION_FRAC
    max_shares = int(max_dollars / current_price)

    # --- Take minimum of all methods (most conservative wins) ---
    raw_shares = min(atr_shares, kelly_shares, max_shares)
    method_used = "atr" if raw_shares == atr_shares else ("kelly" if raw_shares == kelly_shares else "max_cap")

    # --- REGIME ADJUSTMENT ---
    regime_adjusted = int(raw_shares * regime_multiplier)

    # --- DRAWDOWN SCALING ---
    drawdown_mult = _compute_drawdown_multiplier(portfolio_value, peak_equity)
    if drawdown_mult <= 0.0:
        return _zero_result("circuit breaker — drawdown exceeds 15%")
    drawdown_adjusted = int(regime_adjusted * drawdown_mult)

    if drawdown_adjusted <= 0:
        return _zero_result("position rounded to zero after regime/drawdown scaling")

    # --- CONFIDENCE SCALING ---
    # Scale between 80-100% based on confidence (0.70 → 80%, 1.0 → 100%)
    conf_scale = 0.80 + 0.20 * min(max((confidence - 0.70) / 0.30, 0.0), 1.0)
    final_shares = max(_MIN_SHARES, int(drawdown_adjusted * conf_scale))

    # Final safety: never exceed max
    final_shares = min(final_shares, max_shares)

    dollar_amount = round(final_shares * current_price, 2)
    actual_risk = round(final_shares * stop_distance, 2)

    result = {
        "shares": final_shares,
        "dollar_amount": dollar_amount,
        "position_pct": round(dollar_amount / portfolio_value * 100, 2),
        "risk_dollars": actual_risk,
        "risk_pct": round(actual_risk / portfolio_value * 100, 2),
        "stop_price": atr_stop_price,
        "stop_distance": round(stop_distance, 2),
        "stop_pct": round(stop_distance / current_price * 100, 2),
        "method_used": method_used,
        "sizing_details": {
            "atr_shares": atr_shares,
            "kelly_shares": kelly_shares,
            "max_cap_shares": max_shares,
            "raw_shares": raw_shares,
            "regime_mult": regime_multiplier,
            "regime_adjusted": regime_adjusted,
            "drawdown_mult": round(drawdown_mult, 2),
            "drawdown_adjusted": drawdown_adjusted,
            "confidence_scale": round(conf_scale, 2),
            "kelly_pct": round(kelly_pct * 100, 2),
            "base_risk_pct": _BASE_RISK_FRAC,
            "atr_stop_multiple": _ATR_STOP_MULTIPLE,
        },
    }

    logger.info(
        "Position size: %d shares ($%.2f, %.1f%% of portfolio) | "
        "method=%s risk=$%.2f (%.2f%%) stop=%.2f (%.1f%%) | "
        "regime=%.1f drawdown=%.2f conf=%.2f",
        final_shares, dollar_amount, result["position_pct"],
        method_used, actual_risk, result["risk_pct"],
        atr_stop_price, result["stop_pct"],
        regime_multiplier, drawdown_mult, conf_scale,
    )

    return result


def compute_partial_exit_plan(
    shares: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
) -> dict[str, Any]:
    """
    Generate a 3-tier partial profit-taking plan.

    Tier 1: Sell 1/3 at +1R (risk:reward 1:1) — lock in breakeven
    Tier 2: Sell 1/3 at +2R — take solid profit
    Tier 3: Let 1/3 run with tight trailing stop — capture outlier moves

    Args:
        shares: Total shares in position
        entry_price: Entry price
        stop_price: Initial stop price
        target_price: Full target price

    Returns:
        Dict with exit tiers and their share counts + prices
    """
    risk = entry_price - stop_price
    if risk <= 0:
        risk = entry_price * 0.05

    tier1_shares = max(1, shares // 3)
    tier2_shares = max(1, shares // 3)
    tier3_shares = shares - tier1_shares - tier2_shares

    plan = {
        "total_shares": shares,
        "tiers": [
            {
                "tier": 1,
                "shares": tier1_shares,
                "target_price": round(entry_price + risk, 2),
                "description": "Lock in breakeven — sell 1/3 at +1R",
                "trail_after": None,
            },
            {
                "tier": 2,
                "shares": tier2_shares,
                "target_price": round(entry_price + 2 * risk, 2),
                "description": "Take profit — sell 1/3 at +2R",
                "trail_after": None,
            },
            {
                "tier": 3,
                "shares": tier3_shares,
                "target_price": target_price,
                "description": "Runner — trail at 5% after +2R hit",
                "trail_after": 5.0,
            },
        ],
        "breakeven_move_after_tier1": True,
    }

    return plan


def _compute_kelly(win_rate: float, avg_win_loss_ratio: float) -> float:
    """
    Compute fractional Kelly criterion position size.

    Kelly % = (W × R - (1 - W)) / R
    where W = win rate, R = avg_win / avg_loss

    Returns fraction of portfolio to bet (0.0 to MAX_POSITION_PCT/100).
    """
    if avg_win_loss_ratio <= 0 or win_rate <= 0:
        return 0.01  # minimum 1%

    kelly = (win_rate * avg_win_loss_ratio - (1 - win_rate)) / avg_win_loss_ratio

    # Apply fractional Kelly
    fractional = kelly * _KELLY_FRACTION

    # Clamp between 1% and max position fraction
    return max(0.01, min(fractional, _MAX_POSITION_FRAC))


def _compute_drawdown_multiplier(portfolio_value: float, peak_equity: float) -> float:
    """
    Scale position size down as drawdown deepens.

    Drawdown 0-3%: full size (1.0)
    Drawdown 3-5%: 90% size
    Drawdown 5-10%: linear scale 80% → 50%
    Drawdown 10-15%: 50% → 30%
    Drawdown >15%: circuit breaker territory (return 0)
    """
    if peak_equity <= 0 or portfolio_value >= peak_equity:
        return 1.0

    drawdown_pct = (peak_equity - portfolio_value) / peak_equity * 100

    if drawdown_pct <= 3.0:
        return 1.0
    elif drawdown_pct <= 5.0:
        return 0.90
    elif drawdown_pct <= 10.0:
        # Linear interpolation: 80% at 5% DD → 50% at 10% DD
        return 0.80 - (drawdown_pct - 5.0) * 0.06
    elif drawdown_pct <= 15.0:
        return 0.50 - (drawdown_pct - 10.0) * 0.04
    else:
        logger.warning("Drawdown %.1f%% exceeds 15%% — circuit breaker territory", drawdown_pct)
        return 0.0


def _zero_result(reason: str) -> dict[str, Any]:
    logger.info("Position size = 0: %s", reason)
    return {
        "shares": 0,
        "dollar_amount": 0.0,
        "position_pct": 0.0,
        "risk_dollars": 0.0,
        "risk_pct": 0.0,
        "stop_price": 0.0,
        "stop_distance": 0.0,
        "stop_pct": 0.0,
        "method_used": "blocked",
        "sizing_details": {"reason": reason},
    }
