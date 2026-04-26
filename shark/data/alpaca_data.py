"""
shark/data/alpaca_data.py
--------------------------
Thin wrappers around the Alpaca Trade API for account info, positions,
historical OHLCV bars, and live quotes.

Environment variables required
-------------------------------
ALPACA_API_KEY      – Alpaca public key
ALPACA_SECRET_KEY   – Alpaca secret key
ALPACA_BASE_URL     – (optional) defaults to https://paper-api.alpaca.markets

The REST client is initialised lazily — module import will not raise even if
the environment variables are absent; the error is deferred until the first
function call.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy client initialisation
# ---------------------------------------------------------------------------

_rest_client: Any = None  # alpaca_trade_api.REST instance


def _get_client() -> Any:
    """Return (and lazily create) the Alpaca REST client.

    Raises
    ------
    EnvironmentError
        If ``ALPACA_API_KEY`` or ``ALPACA_SECRET_KEY`` are not set.
    """
    global _rest_client
    if _rest_client is not None:
        return _rest_client

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    base_url = os.environ.get(
        "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
    )

    if not api_key:
        raise EnvironmentError(
            "ALPACA_API_KEY environment variable is not set. "
            "Set it to your Alpaca public key before calling any data function."
        )
    if not secret_key:
        raise EnvironmentError(
            "ALPACA_SECRET_KEY environment variable is not set. "
            "Set it to your Alpaca secret key before calling any data function."
        )

    try:
        import alpaca_trade_api as tradeapi  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "alpaca-trade-api is not installed. "
            "Run: pip install alpaca-trade-api"
        ) from exc

    _rest_client = tradeapi.REST(
        key_id=api_key,
        secret_key=secret_key,
        base_url=base_url,
        api_version="v2",
    )
    logger.debug("Alpaca REST client initialised (base_url=%s)", base_url)
    return _rest_client


# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------

_TIMEFRAME_MAP: dict[str, str] = {
    "1Min": "1Min",
    "5Min": "5Min",
    "15Min": "15Min",
    "1Hour": "1Hour",
    "1Day": "1Day",
}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def get_account() -> dict[str, Any]:
    """Return key account metrics from Alpaca.

    Returns
    -------
    dict
        Keys: ``equity`` (float), ``cash`` (float),
        ``buying_power`` (float), ``portfolio_value`` (float),
        ``daytrade_count`` (int).

    Raises
    ------
    EnvironmentError
        If API keys are missing.
    """
    api = _get_client()
    acct = api.get_account()

    return {
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "buying_power": float(acct.buying_power),
        "portfolio_value": float(acct.portfolio_value),
        "daytrade_count": int(acct.daytrade_count),
    }


def get_positions() -> list[dict[str, Any]]:
    """Return all open positions.

    Returns
    -------
    list[dict]
        Each dict contains: ``symbol``, ``qty`` (float),
        ``avg_entry_price`` (float), ``current_price`` (float),
        ``unrealized_pl`` (float), ``unrealized_plpc`` (float),
        ``market_value`` (float), ``side`` (str).
        Returns an empty list when there are no open positions.

    Raises
    ------
    EnvironmentError
        If API keys are missing.
    """
    api = _get_client()

    try:
        positions = api.list_positions()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch positions: %s", exc)
        return []

    result: list[dict[str, Any]] = []
    for pos in positions:
        result.append(
            {
                "symbol": pos.symbol,
                "qty": float(pos.qty),
                "avg_entry_price": float(pos.avg_entry_price),
                "current_price": float(pos.current_price),
                "unrealized_pl": float(pos.unrealized_pl),
                "unrealized_plpc": float(pos.unrealized_plpc),
                "market_value": float(pos.market_value),
                "side": pos.side,
            }
        )

    return result


def get_bars(
    symbol: str,
    timeframe: str = "1Day",
    limit: int = 60,
) -> pd.DataFrame:
    """Fetch historical OHLCV bars for a symbol.

    Parameters
    ----------
    symbol:
        Uppercase ticker symbol, e.g. ``"AAPL"``.
    timeframe:
        One of ``"1Min"``, ``"5Min"``, ``"15Min"``, ``"1Hour"``, ``"1Day"``.
        Defaults to ``"1Day"``.
    limit:
        Number of bars to retrieve. Defaults to 60.

    Returns
    -------
    pd.DataFrame
        Columns: ``timestamp`` (datetime, UTC), ``open``, ``high``,
        ``low``, ``close``, ``volume`` (all float).  Index is a plain
        RangeIndex; bars are sorted oldest-first.

    Raises
    ------
    ValueError
        If *timeframe* is not one of the supported values.
    EnvironmentError
        If API keys are missing.
    """
    if timeframe not in _TIMEFRAME_MAP:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. "
            f"Choose from: {list(_TIMEFRAME_MAP)}"
        )

    api = _get_client()

    # The alpaca-trade-api library's get_bars() method accepts the timeframe
    # as a string and a limit parameter.
    bars = api.get_bars(
        symbol,
        _TIMEFRAME_MAP[timeframe],
        limit=limit,
        adjustment="raw",
    ).df

    if bars.empty:
        logger.warning("No bars returned for symbol=%s timeframe=%s", symbol, timeframe)
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    # The library returns a DataFrame whose index is a DatetimeIndex named 't'.
    # Normalise it to a plain column.
    bars = bars.reset_index()

    # Rename columns to our standard schema
    rename_map: dict[str, str] = {}
    for col in bars.columns:
        col_lower = col.lower()
        if col_lower in ("t", "timestamp", "time"):
            rename_map[col] = "timestamp"
        elif col_lower == "o":
            rename_map[col] = "open"
        elif col_lower == "h":
            rename_map[col] = "high"
        elif col_lower == "l":
            rename_map[col] = "low"
        elif col_lower == "c":
            rename_map[col] = "close"
        elif col_lower == "v":
            rename_map[col] = "volume"
        # keep other columns as-is

    bars = bars.rename(columns=rename_map)

    # Ensure standard columns exist; fill missing ones with NaN
    for col in ("timestamp", "open", "high", "low", "close", "volume"):
        if col not in bars.columns:
            bars[col] = float("nan")

    bars = bars[["timestamp", "open", "high", "low", "close", "volume"]].copy()

    # Cast numeric columns
    for col in ("open", "high", "low", "close", "volume"):
        bars[col] = pd.to_numeric(bars[col], errors="coerce")

    # Ensure timestamp is tz-aware UTC
    if not pd.api.types.is_datetime64_any_dtype(bars["timestamp"]):
        bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    elif bars["timestamp"].dt.tz is None:
        bars["timestamp"] = bars["timestamp"].dt.tz_localize("UTC")

    bars = bars.sort_values("timestamp").reset_index(drop=True)
    return bars


def get_watchlist_snapshot(tickers: list[str]) -> list[dict[str, Any]]:
    """Fetch the latest quote snapshot for each ticker in *tickers*.

    Tickers that produce an error are skipped with a warning log — they do
    not cause the whole call to fail.

    Parameters
    ----------
    tickers:
        List of uppercase ticker symbols.

    Returns
    -------
    list[dict]
        Each dict contains: ``symbol``, ``bid`` (float), ``ask`` (float),
        ``last_price`` (float), ``change_pct`` (float), ``volume`` (float).

    Raises
    ------
    EnvironmentError
        If API keys are missing.
    """
    api = _get_client()
    result: list[dict[str, Any]] = []

    for ticker in tickers:
        try:
            snapshot = api.get_snapshot(ticker)

            # latest_trade gives last_price
            last_price: float = float(
                getattr(snapshot.latest_trade, "p", 0.0)
                if snapshot.latest_trade
                else 0.0
            )

            # latest_quote gives bid / ask
            bid: float = float(
                getattr(snapshot.latest_quote, "bp", 0.0)
                if snapshot.latest_quote
                else 0.0
            )
            ask: float = float(
                getattr(snapshot.latest_quote, "ap", 0.0)
                if snapshot.latest_quote
                else 0.0
            )

            # daily_bar gives volume and change_pct
            daily_open: float = float(
                getattr(snapshot.daily_bar, "o", 0.0)
                if snapshot.daily_bar
                else 0.0
            )
            volume: float = float(
                getattr(snapshot.daily_bar, "v", 0.0)
                if snapshot.daily_bar
                else 0.0
            )

            # Calculate percentage change vs. daily open
            if daily_open and daily_open != 0:
                change_pct = round((last_price - daily_open) / daily_open * 100, 4)
            else:
                change_pct = 0.0

            result.append(
                {
                    "symbol": ticker.upper(),
                    "bid": bid,
                    "ask": ask,
                    "last_price": last_price,
                    "change_pct": change_pct,
                    "volume": volume,
                }
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Skipping ticker %s — snapshot fetch failed: %s", ticker, exc
            )

    return result
