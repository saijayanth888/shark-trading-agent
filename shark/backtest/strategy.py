"""
shark/backtest/strategy.py
----------------------------
Encodes all trading rules from TRADING-STRATEGY.md as testable, deterministic
logic for the backtesting engine.

Every rule mirrors the live system:
  - Regime gating
  - Momentum score threshold
  - RS filtering
  - ATR-based position sizing
  - Entry criteria checks
  - Exit management (stops, partials, time decay, volatility expansion)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trade representation
# ---------------------------------------------------------------------------

class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED_STOP = "CLOSED_STOP"
    CLOSED_HARD_STOP = "CLOSED_HARD_STOP"
    CLOSED_PARTIAL_COMPLETE = "CLOSED_PARTIAL_COMPLETE"
    CLOSED_TIME_DECAY = "CLOSED_TIME_DECAY"
    CLOSED_VOL_EXPANSION = "CLOSED_VOL_EXPANSION"
    CLOSED_REGIME_SHIFT = "CLOSED_REGIME_SHIFT"
    CLOSED_TARGET = "CLOSED_TARGET"


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    shares: int
    stop_price: float
    atr_at_entry: float
    regime_at_entry: str
    momentum_score: float
    rs_composite: float

    # Mutable state
    remaining_shares: int = 0
    realized_pl: float = 0.0
    exit_date: str = ""
    exit_price: float = 0.0
    status: TradeStatus = TradeStatus.OPEN
    days_held: int = 0
    peak_price: float = 0.0
    partial_exits: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.remaining_shares = self.shares
        self.peak_price = self.entry_price


# ---------------------------------------------------------------------------
# Regime detection (offline — uses preloaded SPY data)
# ---------------------------------------------------------------------------

def detect_regime_at(spy_df: pd.DataFrame, bar_index: int) -> dict[str, Any]:
    """Classify market regime at a specific bar index using SPY data."""
    if bar_index < 50:
        return {"regime": "UNKNOWN", "new_trades_allowed": True, "size_mult": 0.5}

    window = spy_df.iloc[:bar_index + 1]
    close = window["close"].astype(float)
    high = window["high"].astype(float)
    low = window["low"].astype(float)

    current = float(close.iloc[-1])
    sma_20 = float(close.rolling(20).mean().iloc[-1])
    sma_50 = float(close.rolling(50).mean().iloc[-1])

    # Trend score
    trend_score = 0
    if current > sma_20:
        trend_score += 1
    else:
        trend_score -= 1
    if current > sma_50:
        trend_score += 1
    else:
        trend_score -= 1
    if sma_20 > sma_50:
        trend_score += 1
    else:
        trend_score -= 1

    is_bullish = trend_score >= 1

    # Volatility
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_14 = float(tr.rolling(14).mean().iloc[-1])
    atr_pct = (atr_14 / current) * 100 if current > 0 else 0

    is_high_vol = atr_pct > 1.5

    if is_bullish and not is_high_vol:
        regime = "BULL_QUIET"
        return {"regime": regime, "new_trades_allowed": True, "size_mult": 1.0}
    elif is_bullish and is_high_vol:
        regime = "BULL_VOLATILE"
        return {"regime": regime, "new_trades_allowed": True, "size_mult": 0.5}
    elif not is_bullish and not is_high_vol:
        regime = "BEAR_QUIET"
        return {"regime": regime, "new_trades_allowed": False, "size_mult": 0.0}
    else:
        regime = "BEAR_VOLATILE"
        return {"regime": regime, "new_trades_allowed": False, "size_mult": 0.0}


# ---------------------------------------------------------------------------
# Technical indicators (offline — mirrors shark/data/technical.py)
# ---------------------------------------------------------------------------

def compute_indicators_at(df: pd.DataFrame, bar_index: int) -> dict[str, Any] | None:
    """Compute technical indicators at a specific bar index."""
    if bar_index < 34:
        return None

    window = df.iloc[:bar_index + 1]
    close = window["close"].astype(float)
    high = window["high"].astype(float)
    low = window["low"].astype(float)
    volume = window["volume"].astype(float)

    n = len(close)
    current_price = float(close.iloc[-1])

    # SMA
    sma_20 = float(close.rolling(20).mean().iloc[-1])
    sma_50 = float(close.rolling(50).mean().iloc[-1]) if n >= 50 else None

    # RSI-14
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_gain = float(gains.rolling(14).mean().iloc[-1])
    avg_loss = float(losses.rolling(14).mean().iloc[-1])
    if avg_loss == 0:
        rsi_14 = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_14 = 100 - (100 / (1 + rs))

    # ATR-14
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_14 = float(tr.rolling(14).mean().iloc[-1])

    # Volume ratio
    vol_sma = float(volume.rolling(20).mean().iloc[-1])
    vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma > 0 else 1.0

    # MACD
    if n >= 35:
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        macd_signal = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = float((macd_line - macd_signal).iloc[-1])
    else:
        macd_hist = 0.0

    # EMA-9
    ema_9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])

    # VWAP
    typical = (high + low + close) / 3
    vwap = float((typical * volume).sum() / volume.sum()) if float(volume.sum()) > 0 else current_price

    # ADX (simplified)
    adx = 25.0  # default
    if n >= 28:
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        atr_s = tr.ewm(span=14, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr_s)
        minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr_s)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1) * 100
        adx = float(dx.ewm(span=14, adjust=False).mean().iloc[-1])

    # Momentum score (mirrors technical.py)
    momentum_score = 0.0
    if current_price > sma_20:
        momentum_score += 15
    if sma_50 is not None and current_price > sma_50:
        momentum_score += 15
    if current_price > vwap:
        momentum_score += 10
    if 40 <= rsi_14 <= 65:
        momentum_score += 15
    elif rsi_14 < 40:
        momentum_score += 5
    if macd_hist > 0:
        momentum_score += 15
    if vol_ratio > 1.2:
        momentum_score += 10
    if adx > 25:
        momentum_score += 10
    if current_price > ema_9:
        momentum_score += 10

    return {
        "current_price": current_price,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "rsi_14": round(rsi_14, 2),
        "atr_14": round(atr_14, 4),
        "volume_ratio": round(vol_ratio, 2),
        "macd_histogram": round(macd_hist, 4),
        "adx": round(adx, 2),
        "momentum_score": round(momentum_score, 1),
    }


# ---------------------------------------------------------------------------
# Relative strength (offline — precomputed)
# ---------------------------------------------------------------------------

def compute_rs_at(stock_df: pd.DataFrame, spy_df: pd.DataFrame, bar_index: int) -> float:
    """Compute RS composite at a specific bar index."""
    if bar_index < 50:
        return 0.0

    stock_close = stock_df["close"].astype(float).iloc[:bar_index + 1]
    spy_close = spy_df["close"].astype(float).iloc[:bar_index + 1]

    min_len = min(len(stock_close), len(spy_close))
    if min_len < 50:
        return 0.0

    stock_close = stock_close.iloc[-min_len:]
    spy_close = spy_close.iloc[-min_len:]

    rs_scores = {}
    weights = {10: 0.40, 20: 0.35, 50: 0.25}

    for period, weight in weights.items():
        if min_len < period:
            rs_scores[period] = 0.0
            continue
        stock_ret = (float(stock_close.iloc[-1]) / float(stock_close.iloc[-period]) - 1) * 100
        spy_ret = (float(spy_close.iloc[-1]) / float(spy_close.iloc[-period]) - 1) * 100
        rs_scores[period] = stock_ret - spy_ret

    composite = sum(rs_scores.get(p, 0.0) * w for p, w in weights.items())
    return round(composite, 2)


# ---------------------------------------------------------------------------
# Entry check — mirrors TRADING-STRATEGY.md entry criteria
# ---------------------------------------------------------------------------

def check_entry(
    indicators: dict[str, Any],
    regime: dict[str, Any],
    rs_composite: float,
    momentum_min: float = 40.0,
    rs_min: float = 0.0,
) -> dict[str, Any]:
    """
    Check all entry criteria. Returns pass/fail + reasons.

    Criteria:
      1. Regime allows new trades
      2. Momentum score >= threshold
      3. RS composite > min threshold
      4. RSI in range (not overbought >80)
      5. Price above SMA-20
    """
    reasons: list[str] = []
    passed = True

    # 1. Regime gate
    if not regime.get("new_trades_allowed", False):
        passed = False
        reasons.append(f"regime={regime['regime']} blocks new trades")

    # 2. Momentum score
    score = indicators.get("momentum_score", 0)
    if score < momentum_min:
        passed = False
        reasons.append(f"momentum={score} < {momentum_min}")

    # 3. Relative strength
    if rs_composite < rs_min:
        passed = False
        reasons.append(f"rs={rs_composite} < {rs_min}")

    # 4. RSI — not overbought
    rsi = indicators.get("rsi_14", 50)
    if rsi > 80:
        passed = False
        reasons.append(f"rsi={rsi} overbought")

    # 5. Price above SMA-20
    price = indicators.get("current_price", 0)
    sma20 = indicators.get("sma_20", 0)
    if price <= sma20:
        passed = False
        reasons.append(f"price={price:.2f} below SMA20={sma20:.2f}")

    return {"passed": passed, "reasons": reasons}


# ---------------------------------------------------------------------------
# Position sizing (offline — mirrors position_sizer.py)
# ---------------------------------------------------------------------------

def compute_shares(
    portfolio_value: float,
    current_price: float,
    atr: float,
    regime_mult: float = 1.0,
    risk_pct: float = 1.0,
    atr_stop_mult: float = 2.0,
    max_position_pct: float = 20.0,
) -> dict[str, Any]:
    """ATR-based position sizing for backtest."""
    if portfolio_value <= 0 or current_price <= 0 or regime_mult <= 0:
        return {"shares": 0, "stop_price": 0, "stop_distance": 0}

    risk_dollars = portfolio_value * (risk_pct / 100)
    stop_distance = max(atr * atr_stop_mult, current_price * 0.02)
    atr_shares = int(risk_dollars / stop_distance)

    max_shares = int(portfolio_value * (max_position_pct / 100) / current_price)

    shares = min(atr_shares, max_shares)
    shares = max(1, int(shares * regime_mult))
    shares = min(shares, max_shares)

    stop_price = round(current_price - stop_distance, 2)

    return {
        "shares": shares,
        "stop_price": stop_price,
        "stop_distance": round(stop_distance, 2),
    }


# ---------------------------------------------------------------------------
# Exit management — mirrors TRADING-STRATEGY.md exit rules
# ---------------------------------------------------------------------------

def check_exits(
    trade: Trade,
    current_price: float,
    current_atr: float,
    regime: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Check all exit conditions for an open trade. Returns list of exit actions.

    Exit rules:
      1. Hard stop: -7% from entry → close all
      2. Trailing stop: trail based on position P&L
      3. Partial profit: T1 at +5%, T2 at +10%, T3 at +15%
      4. Time decay: no +3% after 5 days → close
      5. Volatility expansion: ATR > 2× entry ATR → close
      6. Regime shift to BEAR → close all
    """
    actions: list[dict[str, Any]] = []

    if trade.remaining_shares <= 0:
        return actions

    pnl_pct = (current_price - trade.entry_price) / trade.entry_price * 100
    trade.peak_price = max(trade.peak_price, current_price)
    drawdown_from_peak = (trade.peak_price - current_price) / trade.peak_price * 100

    # 1. Hard stop: -7%
    if pnl_pct <= -7.0:
        actions.append({
            "action": "close_all",
            "reason": "hard_stop",
            "price": current_price,
            "shares": trade.remaining_shares,
            "detail": f"P&L {pnl_pct:.1f}% hit -7% hard stop",
        })
        return actions  # hard stop overrides everything

    # 2. ATR trailing stop
    if current_price <= trade.stop_price:
        actions.append({
            "action": "close_all",
            "reason": "stop_hit",
            "price": current_price,
            "shares": trade.remaining_shares,
            "detail": f"Price {current_price:.2f} hit stop {trade.stop_price:.2f}",
        })
        return actions

    # 3. Regime shift to BEAR
    if regime.get("regime", "").startswith("BEAR") and trade.regime_at_entry.startswith("BULL"):
        actions.append({
            "action": "close_all",
            "reason": "regime_shift",
            "price": current_price,
            "shares": trade.remaining_shares,
            "detail": f"Regime shifted {trade.regime_at_entry} → {regime['regime']}",
        })
        return actions

    # 4. Volatility expansion: current ATR > 2× entry ATR
    if current_atr > 0 and trade.atr_at_entry > 0:
        if current_atr > 2.0 * trade.atr_at_entry:
            actions.append({
                "action": "close_all",
                "reason": "vol_expansion",
                "price": current_price,
                "shares": trade.remaining_shares,
                "detail": f"ATR expanded {trade.atr_at_entry:.2f} → {current_atr:.2f} (>{2*trade.atr_at_entry:.2f})",
            })
            return actions

    # 5. Time decay: no +3% move after 5 days
    if trade.days_held >= 5 and pnl_pct < 3.0:
        actions.append({
            "action": "close_all",
            "reason": "time_decay",
            "price": current_price,
            "shares": trade.remaining_shares,
            "detail": f"Day {trade.days_held}, P&L only {pnl_pct:.1f}% (< +3%)",
        })
        return actions

    # 6. Partial profit-taking
    t1_done = any(p.get("tier") == 1 for p in trade.partial_exits)
    t2_done = any(p.get("tier") == 2 for p in trade.partial_exits)
    t3_done = any(p.get("tier") == 3 for p in trade.partial_exits)

    if pnl_pct >= 5.0 and not t1_done and trade.remaining_shares > 1:
        sell_qty = max(1, trade.remaining_shares // 4)
        actions.append({
            "action": "partial_sell",
            "reason": "partial_T1",
            "price": current_price,
            "shares": sell_qty,
            "tier": 1,
            "detail": f"T1: +{pnl_pct:.1f}%, selling {sell_qty} shares (25%)",
        })

    if pnl_pct >= 10.0 and not t2_done and trade.remaining_shares > 1:
        sell_qty = max(1, trade.remaining_shares // 3)
        actions.append({
            "action": "partial_sell",
            "reason": "partial_T2",
            "price": current_price,
            "shares": sell_qty,
            "tier": 2,
            "detail": f"T2: +{pnl_pct:.1f}%, selling {sell_qty} shares (33%)",
        })

    if pnl_pct >= 15.0 and not t3_done and trade.remaining_shares > 1:
        sell_qty = max(1, trade.remaining_shares // 2)
        actions.append({
            "action": "partial_sell",
            "reason": "partial_T3",
            "price": current_price,
            "shares": sell_qty,
            "tier": 3,
            "detail": f"T3: +{pnl_pct:.1f}%, selling {sell_qty} shares (50%)",
        })

    # 7. Trailing stop update (dynamic)
    if pnl_pct >= 20.0:
        trail_pct = 5.0
    elif pnl_pct >= 15.0:
        trail_pct = 7.0
    elif pnl_pct >= 0:
        trail_pct = 10.0
    else:
        trail_pct = None

    if trail_pct is not None:
        new_stop = round(trade.peak_price * (1 - trail_pct / 100), 2)
        if new_stop > trade.stop_price:
            trade.stop_price = new_stop

    return actions
