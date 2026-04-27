"""
shark/data/watchlist.py
------------------------
Unified watchlist management — single source of truth for all ticker lists.

Three tiers:
  Tier 1 (Core)    — hardcoded in TRADING-STRATEGY.md, always scanned
  Tier 2 (Dynamic) — LLM-discovered weekly, stored in DYNAMIC-WATCHLIST.md
  Tier 3 (Sector)  — sector-to-ETF and ticker-to-sector mappings

All other modules import from here instead of maintaining their own lists.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STRATEGY_PATH = _REPO_ROOT / "memory" / "TRADING-STRATEGY.md"
_DYNAMIC_PATH = _REPO_ROOT / "memory" / "DYNAMIC-WATCHLIST.md"

# ---------------------------------------------------------------------------
# Tier 1 — Core watchlist (fallback if TRADING-STRATEGY.md is unreadable)
# ---------------------------------------------------------------------------

_CORE_FALLBACK: list[str] = [
    "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMD", "AVGO",
    "JPM", "GS", "MS",
    "UNH", "LLY", "JNJ",
    "XOM", "CVX",
    "AMZN", "TSLA",
]

# ---------------------------------------------------------------------------
# Tier 3 — Sector mappings (used by market_open, discovery guardrails)
# ---------------------------------------------------------------------------

SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Consumer Staples": "XLP",
}

# Core ticker → sector (known mappings)
_CORE_TICKER_SECTOR: dict[str, str] = {
    "NVDA": "Technology", "MSFT": "Technology", "AAPL": "Technology",
    "GOOGL": "Technology", "META": "Technology", "AMD": "Technology",
    "AVGO": "Technology", "PLTR": "Technology",
    "JPM": "Financials", "GS": "Financials", "MS": "Financials",
    "UNH": "Healthcare", "LLY": "Healthcare", "JNJ": "Healthcare",
    "XOM": "Energy", "CVX": "Energy",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
}

# Maximum dynamic tickers at any time
MAX_DYNAMIC_TICKERS = 10
# Dynamic tickers expire after this many days
DYNAMIC_EXPIRY_DAYS = 14
# Allowed sectors for dynamic picks
ALLOWED_SECTORS = set(SECTOR_ETFS.keys())


# ---------------------------------------------------------------------------
# Core watchlist reader
# ---------------------------------------------------------------------------

def get_core_watchlist() -> list[str]:
    """Parse Tier 1 tickers from TRADING-STRATEGY.md. Falls back to hardcoded list."""
    try:
        text = _STRATEGY_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Could not read TRADING-STRATEGY.md — using fallback core watchlist")
        return list(_CORE_FALLBACK)

    tickers: list[str] = []
    in_watchlist_section = False

    for line in text.splitlines():
        stripped = line.strip()

        # Detect watchlist section
        if stripped.startswith("## Watchlist"):
            in_watchlist_section = True
            continue
        if in_watchlist_section and stripped.startswith("## ") and "Watchlist" not in stripped:
            break  # Reached next section

        if not in_watchlist_section:
            continue

        # Match "- TICKER, TICKER2" bullet lines
        bullet = re.match(r"^-\s+([A-Z]{1,5}(?:,\s*[A-Z]{1,5})*)", stripped)
        if bullet:
            for t in re.findall(r"[A-Z]{1,5}", bullet.group(1)):
                tickers.append(t)
            continue

        # Match "| TICKER |" table rows
        table = re.match(r"^\|\s*([A-Z]{1,5})\s*\|", stripped)
        if table:
            tickers.append(table.group(1))

    # Deduplicate preserving order
    seen: set[str] = set()
    unique = [t for t in tickers if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]
    return unique if unique else list(_CORE_FALLBACK)


# ---------------------------------------------------------------------------
# Dynamic watchlist reader/writer
# ---------------------------------------------------------------------------

def _parse_dynamic_entries() -> list[dict[str, Any]]:
    """Read DYNAMIC-WATCHLIST.md and return list of entry dicts.

    Each entry: {"symbol": str, "sector": str, "source": str,
                 "added_date": str, "expires_date": str, "reason": str}
    """
    if not _DYNAMIC_PATH.exists():
        return []

    try:
        text = _DYNAMIC_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Could not read DYNAMIC-WATCHLIST.md")
        return []

    # Look for the JSON block between ```json ... ```
    match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if not match:
        return []

    try:
        entries = json.loads(match.group(1))
        if not isinstance(entries, list):
            return []
        return entries
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse DYNAMIC-WATCHLIST.md JSON block")
        return []


def get_dynamic_watchlist() -> list[str]:
    """Return Tier 2 tickers that haven't expired yet."""
    entries = _parse_dynamic_entries()
    today = date.today()
    active: list[str] = []

    for entry in entries:
        try:
            expires = date.fromisoformat(entry.get("expires_date", "2000-01-01"))
            if expires >= today:
                active.append(entry["symbol"])
        except (ValueError, KeyError):
            continue

    return active


def get_dynamic_entries() -> list[dict[str, Any]]:
    """Return all dynamic entries (including expired) with metadata."""
    return _parse_dynamic_entries()


def save_dynamic_watchlist(entries: list[dict[str, Any]]) -> None:
    """Write dynamic watchlist entries to DYNAMIC-WATCHLIST.md.

    Enforces MAX_DYNAMIC_TICKERS and deduplication.
    """
    # Deduplicate by symbol, keeping latest
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        sym = entry.get("symbol", "")
        if sym:
            seen[sym] = entry
    deduped = list(seen.values())

    # Enforce limit — keep the most recently added
    deduped.sort(
        key=lambda e: e.get("added_date", "2000-01-01"), reverse=True,
    )
    deduped = deduped[:MAX_DYNAMIC_TICKERS]

    today = date.today().isoformat()
    active_count = sum(
        1 for e in deduped
        if e.get("expires_date", "2000-01-01") >= today
    )

    content = f"""# Dynamic Watchlist
> Auto-managed by Shark's weekly discovery engine.
> Do not edit manually — changes will be overwritten.
> Last updated: {today}
> Active tickers: {active_count} / {MAX_DYNAMIC_TICKERS}

## Active Entries

```json
{json.dumps(deduped, indent=2)}
```

## Rules
- Max {MAX_DYNAMIC_TICKERS} dynamic tickers at any time
- Entries expire after {DYNAMIC_EXPIRY_DAYS} days if not traded
- Only stocks with market cap > $10B and avg volume > 1M qualify
- Must map to a tracked sector
"""
    _DYNAMIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DYNAMIC_PATH.write_text(content, encoding="utf-8")
    logger.info(
        "Dynamic watchlist saved: %d entries (%d active)",
        len(deduped), active_count,
    )


# ---------------------------------------------------------------------------
# Unified watchlist (Tier 1 + Tier 2)
# ---------------------------------------------------------------------------

def get_full_watchlist() -> list[str]:
    """Return merged Core + Dynamic watchlist, deduplicated, preserving order.

    Core tickers always come first.
    """
    core = get_core_watchlist()
    dynamic = get_dynamic_watchlist()

    seen = set(core)
    merged = list(core)
    for sym in dynamic:
        if sym not in seen:
            merged.append(sym)
            seen.add(sym)

    return merged


# ---------------------------------------------------------------------------
# Sector lookup (works for both core and dynamic tickers)
# ---------------------------------------------------------------------------

def get_ticker_sector(symbol: str) -> str:
    """Return sector for a ticker.

    Checks core mapping first, then dynamic entries, falls back to 'Technology'.
    """
    if symbol in _CORE_TICKER_SECTOR:
        return _CORE_TICKER_SECTOR[symbol]

    # Check dynamic entries
    for entry in _parse_dynamic_entries():
        if entry.get("symbol") == symbol:
            sector = entry.get("sector", "")
            if sector in ALLOWED_SECTORS:
                return sector

    return "Technology"  # safe default


def get_all_ticker_sectors() -> dict[str, str]:
    """Return a complete ticker → sector mapping for all known tickers."""
    mapping = dict(_CORE_TICKER_SECTOR)

    for entry in _parse_dynamic_entries():
        sym = entry.get("symbol", "")
        sector = entry.get("sector", "")
        if sym and sector and sym not in mapping:
            mapping[sym] = sector

    return mapping
