"""
S&P 500 universe management.

Source of truth is `kb/universe/sp500.json`, refreshed weekly during kb-refresh.
If the cache is missing or stale, falls back to a remote fetch from
github.com/datasets/s-and-p-500-companies (Wikipedia-derived, MIT-licensed).

Usage:
    from shark.data.sp500 import get_sp500_tickers, get_sp500_with_sector

    tickers = get_sp500_tickers()           # ['MMM', 'AOS', 'ABT', ...]
    full = get_sp500_with_sector()           # [{'symbol': 'MMM', 'sector': 'Industrials', ...}, ...]
"""
from __future__ import annotations

import csv
import io
import json
import logging
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CACHE_PATH = _REPO_ROOT / "kb" / "universe" / "sp500.json"

_REMOTE_URL = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/master/data/constituents.csv"
)

# Maximum cache age before we attempt a refresh.
# Weekly refresh is fine — S&P 500 turnover is rare.
_CACHE_TTL_DAYS = 7


def get_sp500_tickers() -> list[str]:
    """Return the list of S&P 500 ticker symbols (uppercase)."""
    data = _load_or_fetch()
    return [row["symbol"] for row in data["constituents"]]


def get_sp500_with_sector() -> list[dict[str, str]]:
    """Return list of dicts with symbol + GICS sector + sub-industry."""
    data = _load_or_fetch()
    return list(data["constituents"])


def get_sp500_by_sector() -> dict[str, list[str]]:
    """Return mapping of GICS sector → list of tickers in that sector."""
    out: dict[str, list[str]] = {}
    for row in get_sp500_with_sector():
        out.setdefault(row["sector"], []).append(row["symbol"])
    return out


def refresh_sp500_cache() -> dict[str, Any]:
    """Force-refresh the cache from the remote source. Returns the new cache."""
    logger.info("Fetching S&P 500 constituents from %s", _REMOTE_URL)
    constituents = _fetch_remote()
    cache = {
        "source": _REMOTE_URL,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "ticker_count": len(constituents),
        "constituents": constituents,
    }
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2) + "\n")
    logger.info("S&P 500 cache refreshed — %d tickers written to %s",
                len(constituents), _CACHE_PATH)
    return cache


def _load_or_fetch() -> dict[str, Any]:
    """Load cache; refresh if missing or stale."""
    if _CACHE_PATH.exists():
        try:
            cache = json.loads(_CACHE_PATH.read_text())
            fetched_at = cache.get("fetched_at", "")
            if fetched_at:
                fetched_dt = datetime.fromisoformat(fetched_at.rstrip("Z"))
                age_days = (datetime.utcnow() - fetched_dt).days
                if age_days < _CACHE_TTL_DAYS:
                    return cache
            logger.info("S&P 500 cache is stale (>%dd) — refreshing", _CACHE_TTL_DAYS)
        except Exception as exc:
            logger.warning("Failed to read S&P 500 cache, refreshing: %s", exc)

    try:
        return refresh_sp500_cache()
    except Exception as exc:
        logger.error("S&P 500 remote fetch failed: %s", exc)
        # Last-resort fallback to whatever cache we have, even if stale
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text())
        raise RuntimeError(
            "No S&P 500 cache and remote fetch failed. "
            "Run `python -c 'from shark.data.sp500 import refresh_sp500_cache; refresh_sp500_cache()'` "
            "with internet access to bootstrap."
        ) from exc


def _fetch_remote() -> list[dict[str, str]]:
    """Download the latest S&P 500 constituents CSV and parse it."""
    req = urllib.request.Request(
        _REMOTE_URL,
        headers={"User-Agent": "shark-trading-agent/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")

    constituents: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        symbol = (row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        # Replace dots with hyphens for tickers like BRK.B -> BRK.B (Alpaca uses BRK.B)
        # Alpaca actually accepts BRK.B as-is, so no transformation needed.
        constituents.append({
            "symbol": symbol,
            "name": (row.get("Security") or "").strip(),
            "sector": (row.get("GICS Sector") or "").strip(),
            "sub_industry": (row.get("GICS Sub-Industry") or "").strip(),
        })

    if len(constituents) < 400:
        raise RuntimeError(
            f"S&P 500 remote returned only {len(constituents)} tickers — refusing to use"
        )
    return constituents


if __name__ == "__main__":
    # Bootstrap: run this module directly to seed the cache.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cache = refresh_sp500_cache()
    print(f"Wrote {cache['ticker_count']} tickers to {_CACHE_PATH}")
    by_sector = get_sp500_by_sector()
    for sector, tickers in sorted(by_sector.items()):
        print(f"  {sector:30s}: {len(tickers):3d} tickers")
