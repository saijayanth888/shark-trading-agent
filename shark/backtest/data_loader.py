"""
shark/backtest/data_loader.py
------------------------------
Fetch and cache historical OHLCV data from Alpaca for backtesting.

Pulls daily bars for each symbol and SPY (benchmark), caches them in-memory
for the duration of the backtest run. Uses the same alpaca_data wrapper as
the live system — no separate API calls.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from shark.data.alpaca_data import get_bars

logger = logging.getLogger(__name__)

# Maximum bars Alpaca returns in one call — we chunk if needed
_MAX_BARS_PER_CALL = 1000
_BENCHMARK = "SPY"


class HistoricalDataLoader:
    """Fetch and hold historical price data for a backtest session."""

    def __init__(self, symbols: list[str], lookback_days: int = 365):
        self.symbols = [s.upper() for s in symbols]
        self.lookback_days = lookback_days
        self._cache: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, pd.DataFrame]:
        """Fetch bars for all symbols + benchmark. Returns symbol→DataFrame map."""
        all_symbols = list(set(self.symbols + [_BENCHMARK]))

        for symbol in all_symbols:
            if symbol in self._cache:
                continue
            df = self._fetch(symbol)
            if df is not None and len(df) >= 20:
                self._cache[symbol] = df
                logger.info("Loaded %d bars for %s", len(df), symbol)
            else:
                logger.warning("Skipping %s — insufficient data", symbol)

        return self._cache

    def get(self, symbol: str) -> pd.DataFrame | None:
        """Return cached bars for a symbol, or fetch if not cached."""
        symbol = symbol.upper()
        if symbol not in self._cache:
            df = self._fetch(symbol)
            if df is not None and len(df) >= 20:
                self._cache[symbol] = df
            else:
                return None
        return self._cache.get(symbol)

    def get_benchmark(self) -> pd.DataFrame | None:
        """Return cached benchmark (SPY) bars."""
        return self.get(_BENCHMARK)

    @property
    def available_symbols(self) -> list[str]:
        return [s for s in self.symbols if s in self._cache]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch(self, symbol: str) -> pd.DataFrame | None:
        """Fetch daily bars from Alpaca via the existing wrapper."""
        try:
            limit = min(self.lookback_days, _MAX_BARS_PER_CALL)
            df = get_bars(symbol, timeframe="1Day", limit=limit)

            if df is None or df.empty:
                return None

            # Ensure required columns
            for col in ("open", "high", "low", "close", "volume"):
                if col not in df.columns:
                    return None

            df = df.sort_values("timestamp").reset_index(drop=True)
            return df

        except Exception as exc:
            logger.error("Failed to fetch bars for %s: %s", symbol, exc)
            return None


def get_default_symbols() -> list[str]:
    """Return the default watchlist from TRADING-STRATEGY.md."""
    return [
        "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMD", "AVGO",
        "JPM", "GS", "MS",
        "UNH", "LLY", "JNJ",
        "XOM", "CVX",
        "AMZN", "TSLA",
    ]
