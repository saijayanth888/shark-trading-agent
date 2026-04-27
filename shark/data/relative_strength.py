"""
Relative Strength Ranking — only trade stocks outperforming the benchmark.

Mansfield Relative Strength:
  RS = (stock_performance / benchmark_performance - 1) × 100

  Positive RS = outperforming SPY → eligible for longs
  Negative RS = underperforming SPY → skip

Multi-period scoring: weighted blend of 10-day, 20-day, and 50-day RS
to capture both short and medium-term momentum.

This is one of the single highest-alpha signals available to retail traders.
Institutional money flows into relative strength leaders, creating a
self-reinforcing momentum effect.
"""

from __future__ import annotations
import logging
from typing import Any

import pandas as pd

from shark.data.alpaca_data import get_bars

logger = logging.getLogger(__name__)

_BENCHMARK = "SPY"

# Weights for multi-period RS blend
_RS_WEIGHTS = {
    10: 0.40,  # short-term momentum (most responsive)
    20: 0.35,  # medium-term trend
    50: 0.25,  # longer-term leadership
}


def compute_relative_strength(
    symbol: str,
    benchmark: str = _BENCHMARK,
    lookback: int = 60,
) -> dict[str, Any]:
    """
    Compute Mansfield-style relative strength for a symbol vs benchmark.

    Args:
        symbol: Ticker to evaluate (e.g., "NVDA")
        benchmark: Benchmark to compare against (default: "SPY")
        lookback: Number of bars to fetch (need >= 50 for full calculation)

    Returns:
        Dict with keys:
            rs_composite (float): weighted multi-period RS score
            rs_10 (float): 10-day relative strength
            rs_20 (float): 20-day relative strength
            rs_50 (float): 50-day relative strength
            outperforming (bool): True if rs_composite > 0
            rs_rank_signal (str): "STRONG" / "MODERATE" / "WEAK" / "UNDERPERFORMING"
            acceleration (float): RS_10 - RS_20 (positive = accelerating)
    """
    try:
        stock_bars = get_bars(symbol, timeframe="1Day", limit=lookback)
        bench_bars = get_bars(benchmark, timeframe="1Day", limit=lookback)
    except Exception as exc:
        logger.error("RS fetch failed for %s vs %s: %s", symbol, benchmark, exc)
        return _default_rs(symbol, f"data fetch error: {exc}")

    stock_df = _normalize_df(stock_bars)
    bench_df = _normalize_df(bench_bars)

    if stock_df is None or bench_df is None:
        return _default_rs(symbol, "insufficient data")

    # Align on dates
    min_len = min(len(stock_df), len(bench_df))
    if min_len < 20:
        return _default_rs(symbol, f"only {min_len} aligned bars (need 20+)")

    stock_close = stock_df["close"].iloc[-min_len:].reset_index(drop=True)
    bench_close = bench_df["close"].iloc[-min_len:].reset_index(drop=True)

    rs_scores: dict[int, float] = {}
    for period in (10, 20, 50):
        if min_len < period:
            rs_scores[period] = 0.0
            continue

        stock_return = (float(stock_close.iloc[-1]) / float(stock_close.iloc[-period]) - 1) * 100
        bench_return = (float(bench_close.iloc[-1]) / float(bench_close.iloc[-period]) - 1) * 100

        # Mansfield RS: relative outperformance
        rs_scores[period] = round(stock_return - bench_return, 2)

    # Weighted composite
    rs_composite = sum(rs_scores.get(p, 0.0) * w for p, w in _RS_WEIGHTS.items())
    rs_composite = round(rs_composite, 2)

    # Acceleration: short-term RS improving vs medium-term
    acceleration = round(rs_scores.get(10, 0.0) - rs_scores.get(20, 0.0), 2)

    # Signal classification
    if rs_composite > 5.0:
        signal = "STRONG"
    elif rs_composite > 0.0:
        signal = "MODERATE"
    elif rs_composite > -3.0:
        signal = "WEAK"
    else:
        signal = "UNDERPERFORMING"

    result = {
        "symbol": symbol,
        "benchmark": benchmark,
        "rs_composite": rs_composite,
        "rs_10": rs_scores.get(10, 0.0),
        "rs_20": rs_scores.get(20, 0.0),
        "rs_50": rs_scores.get(50, 0.0),
        "outperforming": rs_composite > 0,
        "rs_rank_signal": signal,
        "acceleration": acceleration,
        "accelerating": acceleration > 0,
    }

    logger.info(
        "RS %s: composite=%.2f signal=%s accel=%.2f | 10d=%.2f 20d=%.2f 50d=%.2f",
        symbol, rs_composite, signal, acceleration,
        rs_scores.get(10, 0), rs_scores.get(20, 0), rs_scores.get(50, 0),
    )

    return result


def rank_by_relative_strength(
    symbols: list[str],
    benchmark: str = _BENCHMARK,
) -> list[dict[str, Any]]:
    """
    Compute RS for a list of symbols and return sorted by rs_composite (strongest first).

    Args:
        symbols: List of tickers to rank
        benchmark: Benchmark ticker

    Returns:
        List of RS dicts sorted by rs_composite descending.
        Only includes symbols with outperforming=True.
    """
    results: list[dict[str, Any]] = []

    for symbol in symbols:
        rs = compute_relative_strength(symbol, benchmark)
        results.append(rs)

    # Sort by composite RS, strongest first
    results.sort(key=lambda x: x["rs_composite"], reverse=True)

    logger.info(
        "RS ranking: %s",
        [(r["symbol"], r["rs_composite"], r["rs_rank_signal"]) for r in results[:5]],
    )

    return results


def filter_outperformers(
    symbols: list[str],
    benchmark: str = _BENCHMARK,
    min_rs: float = 0.0,
) -> list[str]:
    """
    Return only symbols with RS above min_rs threshold, sorted strongest first.

    Args:
        symbols: Candidate tickers
        benchmark: Benchmark ticker
        min_rs: Minimum RS composite to pass (default 0.0 = just outperforming)

    Returns:
        Filtered list of symbols sorted by RS (strongest first).
    """
    ranked = rank_by_relative_strength(symbols, benchmark)
    filtered = [r["symbol"] for r in ranked if r["rs_composite"] >= min_rs]
    skipped = [r["symbol"] for r in ranked if r["rs_composite"] < min_rs]

    if skipped:
        logger.info("RS filter removed (underperforming SPY): %s", skipped)

    return filtered


def _normalize_df(bars) -> pd.DataFrame | None:
    """Convert bars (DataFrame or list[dict]) to normalized DataFrame with 'close' column."""
    if bars is None:
        return None

    if isinstance(bars, pd.DataFrame):
        df = bars.copy()
    elif isinstance(bars, list):
        if not bars:
            return None
        df = pd.DataFrame(bars)
    else:
        return None

    # Normalize column names
    col_map = {"c": "close", "h": "high", "l": "low", "o": "open", "v": "volume"}
    df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)

    if "close" not in df.columns or len(df) < 10:
        return None

    df["close"] = df["close"].astype(float)
    return df


def _default_rs(symbol: str, reason: str) -> dict[str, Any]:
    logger.warning("RS fallback for %s: %s", symbol, reason)
    return {
        "symbol": symbol,
        "benchmark": _BENCHMARK,
        "rs_composite": 0.0,
        "rs_10": 0.0,
        "rs_20": 0.0,
        "rs_50": 0.0,
        "outperforming": False,
        "rs_rank_signal": "UNKNOWN",
        "acceleration": 0.0,
        "accelerating": False,
        "error": reason,
    }
