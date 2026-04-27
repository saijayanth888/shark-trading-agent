"""
shark/data/technical.py
-----------------------
Pure-pandas technical indicator calculations — no external TA library.

All indicators use standard financial definitions:

* SMA   – simple arithmetic mean of closing prices.
* RSI   – Relative Strength Index with Wilder's (EMA-based) smoothing,
           period 14.
* Volume SMA & ratio – 20-period SMA of volume and the ratio of the most
                       recent bar's volume to that average.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """Compute a standard set of technical indicators for a price series.

    Parameters
    ----------
    df:
        DataFrame that **must** contain at minimum the columns
        ``close`` and ``volume``.  At least 20 rows are required; 50+
        rows are needed for a valid SMA-50.  Extra columns are ignored.

    Returns
    -------
    dict
        ``sma_20`` (float), ``sma_50`` (float | None),
        ``rsi_14`` (float), ``volume_sma_20`` (float),
        ``volume_ratio`` (float), ``current_price`` (float),
        and a nested ``signals`` dict with boolean flags.

    Raises
    ------
    ValueError
        If *df* has fewer than 20 rows or is missing required columns.
    """
    _validate_dataframe(df)

    n_rows = len(df)

    close: pd.Series = df["close"].astype(float)
    volume: pd.Series = df["volume"].astype(float)

    current_price = float(close.iloc[-1])

    # ------------------------------------------------------------------
    # SMA-20  (always available — we already checked n_rows >= 20)
    # ------------------------------------------------------------------
    sma_20 = float(close.rolling(window=20).mean().iloc[-1])

    # ------------------------------------------------------------------
    # SMA-50  (only when we have enough data)
    # ------------------------------------------------------------------
    sma_50: float | None
    if n_rows >= 50:
        sma_50 = float(close.rolling(window=50).mean().iloc[-1])
    else:
        sma_50 = None
        logger.debug(
            "Only %d rows available; SMA-50 set to None (need 50).", n_rows
        )

    # ------------------------------------------------------------------
    # RSI-14 with Wilder smoothing
    # ------------------------------------------------------------------
    rsi_14 = _compute_rsi(close, period=14)

    # ------------------------------------------------------------------
    # Volume SMA-20 and ratio
    # ------------------------------------------------------------------
    volume_sma_20 = float(volume.rolling(window=20).mean().iloc[-1])
    current_volume = float(volume.iloc[-1])

    if volume_sma_20 and volume_sma_20 != 0.0:
        volume_ratio = round(current_volume / volume_sma_20, 4)
    else:
        volume_ratio = 0.0

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    above_sma20 = current_price > sma_20
    above_sma50 = (sma_50 is not None) and (current_price > sma_50)
    rsi_oversold = rsi_14 < 40.0
    rsi_neutral = 40.0 <= rsi_14 <= 65.0
    rsi_overbought = rsi_14 > 65.0
    high_volume = volume_ratio > 1.2

    # ------------------------------------------------------------------
    # ATR-14 (Average True Range — volatility measure for position sizing)
    # ------------------------------------------------------------------
    high: pd.Series = df["high"].astype(float) if "high" in df.columns else close
    low: pd.Series = df["low"].astype(float) if "low" in df.columns else close

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_14 = float(tr.rolling(14).mean().iloc[-1]) if n_rows >= 15 else 0.0
    atr_pct = (atr_14 / current_price * 100) if current_price > 0 else 0.0

    # ------------------------------------------------------------------
    # MACD (12, 26, 9) — momentum and trend confirmation
    # ------------------------------------------------------------------
    if n_rows >= 35:
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        macd_signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_histogram = macd_line - macd_signal_line

        macd_val = float(macd_line.iloc[-1])
        macd_signal = float(macd_signal_line.iloc[-1])
        macd_hist = float(macd_histogram.iloc[-1])

        # MACD crossover detection
        macd_bullish_cross = (
            float(macd_line.iloc[-1]) > float(macd_signal_line.iloc[-1])
            and float(macd_line.iloc[-2]) <= float(macd_signal_line.iloc[-2])
        ) if n_rows >= 36 else False
        macd_bearish_cross = (
            float(macd_line.iloc[-1]) < float(macd_signal_line.iloc[-1])
            and float(macd_line.iloc[-2]) >= float(macd_signal_line.iloc[-2])
        ) if n_rows >= 36 else False
    else:
        macd_val = 0.0
        macd_signal = 0.0
        macd_hist = 0.0
        macd_bullish_cross = False
        macd_bearish_cross = False

    # ------------------------------------------------------------------
    # Bollinger Bands (20, 2) — mean reversion + volatility squeeze detection
    # ------------------------------------------------------------------
    bb_mid = sma_20
    bb_std = float(close.rolling(window=20).std().iloc[-1])
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = ((bb_upper - bb_lower) / bb_mid * 100) if bb_mid > 0 else 0.0
    bb_pct_b = ((current_price - bb_lower) / (bb_upper - bb_lower)) if (bb_upper - bb_lower) > 0 else 0.5

    # Squeeze detection: BB width < 4% suggests consolidation → breakout imminent
    bb_squeeze = bb_width < 4.0

    # ------------------------------------------------------------------
    # ADX-14 (Average Directional Index — trend strength, not direction)
    # ------------------------------------------------------------------
    adx_14 = _compute_adx(high, low, close, period=14) if n_rows >= 28 else 25.0

    # ------------------------------------------------------------------
    # VWAP (Volume Weighted Average Price — institutional reference)
    # ------------------------------------------------------------------
    if "high" in df.columns and "low" in df.columns:
        typical_price = (high + low + close) / 3
        vwap = float((typical_price * volume).sum() / volume.sum()) if float(volume.sum()) > 0 else current_price
    else:
        vwap = current_price

    # ------------------------------------------------------------------
    # EMA-9 (fast trend line for short-term momentum)
    # ------------------------------------------------------------------
    ema_9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])

    # ------------------------------------------------------------------
    # Enhanced Signals
    # ------------------------------------------------------------------
    above_sma20 = current_price > sma_20
    above_sma50 = (sma_50 is not None) and (current_price > sma_50)
    above_vwap = current_price > vwap
    above_ema9 = current_price > ema_9
    rsi_oversold = rsi_14 < 40.0
    rsi_neutral = 40.0 <= rsi_14 <= 65.0
    rsi_overbought = rsi_14 > 65.0
    high_volume = volume_ratio > 1.2
    strong_trend = adx_14 > 25.0
    very_strong_trend = adx_14 > 40.0
    macd_bullish = macd_hist > 0

    # Composite momentum score (0-100)
    momentum_score = 0.0
    if above_sma20:
        momentum_score += 15
    if above_sma50:
        momentum_score += 15
    if above_vwap:
        momentum_score += 10
    if rsi_neutral:
        momentum_score += 15
    elif rsi_oversold:
        momentum_score += 5
    if macd_bullish:
        momentum_score += 15
    if high_volume:
        momentum_score += 10
    if strong_trend:
        momentum_score += 10
    if above_ema9:
        momentum_score += 10

    return {
        "sma_20": sma_20,
        "sma_50": sma_50,
        "ema_9": ema_9,
        "rsi_14": rsi_14,
        "atr_14": round(atr_14, 4),
        "atr_pct": round(atr_pct, 2),
        "macd": round(macd_val, 4),
        "macd_signal": round(macd_signal, 4),
        "macd_histogram": round(macd_hist, 4),
        "macd_bullish_cross": macd_bullish_cross,
        "macd_bearish_cross": macd_bearish_cross,
        "bb_upper": round(bb_upper, 2),
        "bb_lower": round(bb_lower, 2),
        "bb_mid": round(bb_mid, 2),
        "bb_width": round(bb_width, 2),
        "bb_pct_b": round(bb_pct_b, 4),
        "bb_squeeze": bb_squeeze,
        "adx_14": round(adx_14, 2),
        "vwap": round(vwap, 2),
        "volume_sma_20": volume_sma_20,
        "volume_ratio": volume_ratio,
        "current_price": current_price,
        "momentum_score": round(momentum_score, 1),
        "signals": {
            "above_sma20": above_sma20,
            "above_sma50": above_sma50,
            "above_vwap": above_vwap,
            "above_ema9": above_ema9,
            "rsi_oversold": rsi_oversold,
            "rsi_neutral": rsi_neutral,
            "rsi_overbought": rsi_overbought,
            "high_volume": high_volume,
            "strong_trend": strong_trend,
            "very_strong_trend": very_strong_trend,
            "macd_bullish": macd_bullish,
            "macd_bullish_cross": macd_bullish_cross,
            "bb_squeeze": bb_squeeze,
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_dataframe(df: pd.DataFrame) -> None:
    """Raise ``ValueError`` on bad inputs."""
    if df is None or not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be a pandas DataFrame.")

    required_columns = {"close", "volume"}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(
            f"DataFrame is missing required columns: {sorted(missing)}"
        )

    if len(df) < 20:
        raise ValueError(
            f"Need at least 20 rows for indicators, got {len(df)}."
        )


def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    """Calculate the RSI for the most recent bar using Wilder smoothing.

    Wilder's smoothing is equivalent to an exponential moving average
    with ``alpha = 1 / period``.

    Steps:
    1.  Compute per-bar price changes.
    2.  Separate positive (gains) and negative (losses) changes.
    3.  Seed the first smoothed average as the simple mean of the first
        ``period`` values (standard initialisation).
    4.  Apply Wilder's smoothing for subsequent bars.
    5.  RS = avg_gain / avg_loss; RSI = 100 - 100 / (1 + RS).

    Parameters
    ----------
    close:
        Pandas Series of closing prices, oldest-first.
    period:
        RSI period (default 14).

    Returns
    -------
    float
        RSI value in [0, 100].  Returns 50.0 when there is insufficient
        data (fewer than ``period + 1`` bars).
    """
    if len(close) < period + 1:
        logger.debug("Not enough data for RSI-%d, returning 50.0.", period)
        return 50.0

    delta: pd.Series = close.diff()

    gains: pd.Series = delta.clip(lower=0.0)
    losses: pd.Series = (-delta).clip(lower=0.0)

    # Seed: simple average over the first `period` changes
    # (iloc[1] is the first valid diff value)
    avg_gain = float(gains.iloc[1 : period + 1].mean())
    avg_loss = float(losses.iloc[1 : period + 1].mean())

    # Wilder smoothing for all remaining bars
    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + float(gains.iloc[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses.iloc[i])) / period

    if avg_loss == 0.0:
        # All gains — RSI is at maximum
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 4)


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Calculate ADX (Average Directional Index) — measures trend strength (0-100).

    ADX > 25 = trending market (good for momentum strategies)
    ADX < 20 = ranging/choppy market (avoid momentum, use mean reversion)
    ADX > 40 = very strong trend (ride it with trailing stops)

    Parameters
    ----------
    high, low, close:
        Price series (oldest-first).
    period:
        Smoothing period (default 14).

    Returns
    -------
    float
        ADX value in [0, 100]. Returns 25.0 on insufficient data.
    """
    if len(close) < period * 2:
        return 25.0

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1) * 100
    adx = dx.ewm(span=period, adjust=False).mean()

    return round(float(adx.iloc[-1]), 2)
