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

    return {
        "sma_20": sma_20,
        "sma_50": sma_50,
        "rsi_14": rsi_14,
        "volume_sma_20": volume_sma_20,
        "volume_ratio": volume_ratio,
        "current_price": current_price,
        "signals": {
            "above_sma20": above_sma20,
            "above_sma50": above_sma50,
            "rsi_oversold": rsi_oversold,
            "rsi_neutral": rsi_neutral,
            "rsi_overbought": rsi_overbought,
            "high_volume": high_volume,
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
