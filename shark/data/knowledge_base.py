"""
Knowledge Base — read/write API for kb/ folder.

This is the FAST PATH for trading routines: instead of hitting Alpaca/Perplexity
on every pre-market run, the routines load cached historical bars and statistical
patterns from JSON files committed to the repo.

Public API:
    # Historical price data
    load_historical_bars(symbol, days=None)              -> pd.DataFrame
    save_historical_bars(symbol, bars_df)                -> None
    load_bars_metadata()                                 -> dict

    # Statistical patterns (computed weekly during kb-refresh)
    load_ticker_base_rate(symbol, regime)                -> dict | None
    load_anti_patterns(symbol=None, setup=None)          -> list[dict]
    load_calendar_edge(event_type)                       -> dict | None
    load_sector_rotation()                               -> dict
    load_regime_outcomes()                               -> dict
    load_compiled_lessons(limit=10)                      -> list[dict]

    # Trade ledger (written after each closed trade)
    save_closed_trade(trade_dict)                        -> Path
    load_closed_trades(symbol=None, since_date=None)     -> list[dict]

    # Daily snapshot (written by daily-summary)
    save_daily_snapshot(date_str, snapshot_dict)         -> Path
    load_daily_snapshot(date_str)                        -> dict | None

    # Earnings reactions
    save_earnings_reaction(symbol, quarter_data)         -> None
    load_earnings_history(symbol)                        -> list[dict]

    # Macro events
    save_event_reaction(event_dict)                      -> Path
    load_event_reactions(event_type=None)                -> list[dict]

    # Status / introspection
    kb_status()                                          -> dict
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KB_ROOT = _REPO_ROOT / "kb"

_BARS_DIR = _KB_ROOT / "historical_bars"
_TRADES_DIR = _KB_ROOT / "trades"
_DAILY_DIR = _KB_ROOT / "daily"
_EARNINGS_DIR = _KB_ROOT / "earnings"
_EVENTS_DIR = _KB_ROOT / "events"
_PATTERNS_DIR = _KB_ROOT / "patterns"
_LESSONS_DIR = _KB_ROOT / "lessons"

_BARS_META_PATH = _BARS_DIR / "_meta.json"

# Ensure all dirs exist at import time so write functions don't crash.
for _d in (_BARS_DIR, _TRADES_DIR, _DAILY_DIR, _EARNINGS_DIR,
           _EVENTS_DIR, _PATTERNS_DIR, _LESSONS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# Helpers
# ===========================================================================

def _read_json(path: Path) -> Any:
    """Read JSON, returning None if file is missing or invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _write_json(path: Path, data: Any) -> None:
    """Write JSON atomically with pretty indent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str) + "\n")
    tmp.replace(path)


def _safe_symbol(symbol: str) -> str:
    """Normalize ticker for filename use."""
    return symbol.upper().replace("/", "_")


# ===========================================================================
# Historical Bars
# ===========================================================================

def save_historical_bars(symbol: str, bars: pd.DataFrame) -> None:
    """
    Save 2 years of OHLCV bars for a single ticker.

    Args:
        symbol: ticker
        bars: DataFrame with columns timestamp, open, high, low, close, volume
    """
    sym = _safe_symbol(symbol)
    if bars.empty:
        logger.warning("save_historical_bars: empty bars for %s — skipping", sym)
        return

    records: list[dict[str, Any]] = []
    for _, row in bars.iterrows():
        ts = row.get("timestamp")
        if ts is None or pd.isna(ts):
            continue
        records.append({
            "date": str(pd.to_datetime(ts).date()),
            "o": round(float(row["open"]), 4),
            "h": round(float(row["high"]), 4),
            "l": round(float(row["low"]), 4),
            "c": round(float(row["close"]), 4),
            "v": int(row.get("volume") or 0),
        })

    payload = {
        "symbol": sym,
        "last_updated": str(date.today()),
        "bar_count": len(records),
        "bars": records,
    }
    _write_json(_BARS_DIR / f"{sym}.json", payload)


def load_historical_bars(symbol: str, days: int | None = None) -> pd.DataFrame:
    """
    Load cached bars for *symbol*. Returns empty DataFrame if not in KB.

    Args:
        symbol: ticker
        days:   if set, return only the most recent N bars (most-recent last)

    Returns:
        DataFrame with columns timestamp (UTC datetime), open, high, low, close, volume
    """
    sym = _safe_symbol(symbol)
    payload = _read_json(_BARS_DIR / f"{sym}.json")
    if not payload or "bars" not in payload:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    rows = payload["bars"]
    if days is not None and len(rows) > days:
        rows = rows[-days:]

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["date"], utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["timestamp", "open", "high", "low", "close", "volume"]].copy()


def load_bars_metadata() -> dict[str, Any]:
    """Return KB-wide bar metadata (when last refreshed, ticker count, etc.)."""
    meta = _read_json(_BARS_META_PATH) or {}
    return meta


def save_bars_metadata(meta: dict[str, Any]) -> None:
    """Save KB-wide bar metadata."""
    meta = {**meta, "saved_at": datetime.utcnow().isoformat() + "Z"}
    _write_json(_BARS_META_PATH, meta)


# ===========================================================================
# Statistical Patterns (read-only from trading routines)
# ===========================================================================

def load_ticker_base_rate(symbol: str, regime: str | None = None) -> dict[str, Any] | None:
    """
    Return historical win rate & expectancy for a ticker (optionally filtered by regime).

    Returns None if no record exists yet (cold start — give benefit of doubt).
    """
    data = _read_json(_PATTERNS_DIR / "ticker_base_rates.json") or {}
    rec = data.get(_safe_symbol(symbol))
    if not rec:
        return None
    if regime:
        return rec.get(regime)
    return rec


def load_anti_patterns(
    symbol: str | None = None,
    setup: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return anti-patterns matching the given filters.

    Anti-patterns are setups that historically FAIL — used to REJECT trades.
    Each entry has: id, description, fade_rate / loss_rate, action, applies_to.
    """
    data = _read_json(_PATTERNS_DIR / "anti_patterns.json") or {}
    matches: list[dict[str, Any]] = []
    for pattern_id, rec in data.items():
        applies = rec.get("applies_to", {})
        if symbol and applies.get("symbol") and applies["symbol"] != _safe_symbol(symbol):
            continue
        if setup and applies.get("setup") and applies["setup"] != setup:
            continue
        matches.append({"id": pattern_id, **rec})
    return matches


def load_calendar_edge(event_type: str) -> dict[str, Any] | None:
    """
    Return historical drift / win rate for a calendar event type.
    e.g. 'pre_fomc_drift', 'post_earnings_drift', 'cpi_day', 'pre_cpi_day'.
    """
    data = _read_json(_PATTERNS_DIR / "calendar_effects.json") or {}
    return data.get(event_type)


def load_sector_rotation() -> dict[str, Any]:
    """Return sector momentum / rotation patterns."""
    return _read_json(_PATTERNS_DIR / "sector_rotation.json") or {}


def load_regime_outcomes() -> dict[str, Any]:
    """Return win-rate / pnl statistics per regime type."""
    return _read_json(_PATTERNS_DIR / "regime_outcomes.json") or {}


def load_compiled_lessons(limit: int = 10) -> list[dict[str, Any]]:
    """Return the top-N most recent lessons compiled from closed trades."""
    data = _read_json(_LESSONS_DIR / "compiled_lessons.json") or {"lessons": []}
    lessons = data.get("lessons", [])
    return lessons[:limit]


# ===========================================================================
# Trade Ledger (append-only, written by daily-summary)
# ===========================================================================

def save_closed_trade(trade: dict[str, Any]) -> Path:
    """
    Save a closed trade to kb/trades/{exit_date}_{symbol}_{side}.json.
    Returns the written path.
    """
    symbol = _safe_symbol(trade.get("ticker") or trade.get("symbol") or "UNKNOWN")
    exit_date = str(trade.get("exit_date") or date.today())
    side = (trade.get("side") or "long").lower()
    path = _TRADES_DIR / f"{exit_date}_{symbol}_{side}.json"
    _write_json(path, trade)
    logger.info("KB trade saved: %s", path.name)
    return path


def load_closed_trades(
    symbol: str | None = None,
    since_date: str | date | None = None,
) -> list[dict[str, Any]]:
    """Load closed trades, optionally filtered."""
    if isinstance(since_date, date):
        since_date = since_date.isoformat()

    out: list[dict[str, Any]] = []
    for path in sorted(_TRADES_DIR.glob("*.json")):
        if path.name.startswith("."):
            continue
        if since_date and path.stem[:10] < since_date:
            continue
        rec = _read_json(path)
        if not rec:
            continue
        if symbol and _safe_symbol(rec.get("ticker", "")) != _safe_symbol(symbol):
            continue
        out.append(rec)
    return out


# ===========================================================================
# Daily Snapshots
# ===========================================================================

def save_daily_snapshot(date_str: str, snapshot: dict[str, Any]) -> Path:
    """Save a daily market snapshot to kb/daily/{date}.json."""
    path = _DAILY_DIR / f"{date_str}.json"
    _write_json(path, snapshot)
    return path


def load_daily_snapshot(date_str: str) -> dict[str, Any] | None:
    """Load a single day's snapshot."""
    return _read_json(_DAILY_DIR / f"{date_str}.json")


# ===========================================================================
# Earnings History
# ===========================================================================

def save_earnings_reaction(symbol: str, quarter_data: dict[str, Any]) -> None:
    """
    Append a single quarter's earnings reaction to the ticker's earnings history.

    quarter_data should include: quarter (e.g. '2026Q1'), report_date,
    surprise_pct, gap_pct, day1_return, day5_return, day20_return.
    """
    sym = _safe_symbol(symbol)
    path = _EARNINGS_DIR / f"{sym}.json"
    existing = _read_json(path) or {"symbol": sym, "quarters": []}

    quarters = existing.get("quarters", [])
    # Replace if same quarter already exists, else append
    quarter_id = quarter_data.get("quarter")
    if quarter_id:
        quarters = [q for q in quarters if q.get("quarter") != quarter_id]
    quarters.append(quarter_data)
    quarters.sort(key=lambda q: q.get("report_date", ""))

    existing["quarters"] = quarters
    existing["last_updated"] = str(date.today())
    _write_json(path, existing)


def load_earnings_history(symbol: str) -> list[dict[str, Any]]:
    """Return list of recent earnings quarters for a ticker."""
    payload = _read_json(_EARNINGS_DIR / f"{_safe_symbol(symbol)}.json")
    if not payload:
        return []
    return list(payload.get("quarters", []))


# ===========================================================================
# Event Reactions (FOMC, CPI, etc.)
# ===========================================================================

def save_event_reaction(event: dict[str, Any]) -> Path:
    """Save a macro event + market reaction to kb/events/{date}_{type}.json."""
    event_date = str(event.get("date") or date.today())
    event_type = (event.get("event_type") or "EVENT").upper()
    path = _EVENTS_DIR / f"{event_date}_{event_type}.json"
    _write_json(path, event)
    return path


def load_event_reactions(event_type: str | None = None) -> list[dict[str, Any]]:
    """Load all event reactions, optionally filtered by type."""
    out: list[dict[str, Any]] = []
    for path in sorted(_EVENTS_DIR.glob("*.json")):
        if path.name.startswith("."):
            continue
        rec = _read_json(path)
        if not rec:
            continue
        if event_type and (rec.get("event_type") or "").upper() != event_type.upper():
            continue
        out.append(rec)
    return out


# ===========================================================================
# Status / introspection
# ===========================================================================

def kb_status() -> dict[str, Any]:
    """Return a summary of what's in the KB. Useful for kb-update logs."""
    bars_files = list(_BARS_DIR.glob("*.json"))
    bars_files = [p for p in bars_files if not p.name.startswith("_")]

    return {
        "bars_tickers": len(bars_files),
        "trades_count": len(list(_TRADES_DIR.glob("*.json"))),
        "daily_snapshots": len(list(_DAILY_DIR.glob("*.json"))),
        "earnings_tickers": len(list(_EARNINGS_DIR.glob("*.json"))),
        "events_count": len(list(_EVENTS_DIR.glob("*.json"))),
        "patterns_files": len(list(_PATTERNS_DIR.glob("*.json"))),
        "bars_meta": load_bars_metadata(),
    }
