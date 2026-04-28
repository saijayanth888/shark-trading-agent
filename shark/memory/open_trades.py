"""
Open-trades sidecar — tracks per-symbol strategy metadata (setup_tag, PEAD
event date) for the lifetime of a position so midday close can attribute
P&L back to the strategy that opened it.

File: memory/open-trades.json
Schema:
{
  "AMD":  {"setup_tag": "pead", "pead_event_date": "2026-04-24",
           "entry_date": "2026-04-28", "entry_price": 336.73},
  "JPM":  {"setup_tag": "momentum", "entry_date": "2026-04-28", ...}
}

API:
    upsert_open_trade(symbol, **kwargs)
    get_open_trade(symbol) -> dict | None
    pop_open_trade(symbol) -> dict | None    # called when trade closes
    list_open_trades() -> dict[str, dict]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_OPEN_TRADES_PATH = _PROJECT_ROOT / "memory" / "open-trades.json"


def _read() -> dict[str, dict[str, Any]]:
    if not _OPEN_TRADES_PATH.exists():
        return {}
    try:
        data = json.loads(_OPEN_TRADES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("open-trades read failed: %s", exc)
        return {}


def _write(data: dict[str, dict[str, Any]]) -> None:
    _OPEN_TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OPEN_TRADES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def upsert_open_trade(symbol: str, **fields: Any) -> None:
    """Insert or update the open-trades sidecar entry for *symbol*."""
    if not symbol:
        return
    data = _read()
    sym = symbol.upper()
    existing = data.get(sym, {})
    existing.update({k: v for k, v in fields.items() if v is not None})
    data[sym] = existing
    _write(data)


def get_open_trade(symbol: str) -> dict[str, Any] | None:
    """Return the stored metadata for *symbol* or None."""
    return _read().get(symbol.upper())


def pop_open_trade(symbol: str) -> dict[str, Any] | None:
    """Return + remove the stored metadata for *symbol*. Used at trade close."""
    data = _read()
    sym = symbol.upper()
    if sym not in data:
        return None
    metadata = data.pop(sym)
    _write(data)
    return metadata


def list_open_trades() -> dict[str, dict[str, Any]]:
    """Return all open-trade sidecar entries."""
    return _read()
