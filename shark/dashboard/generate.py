"""
Dashboard Data Generator — reads memory/ and kb/ files, writes docs/dashboard/data.json.

Called after daily-summary (or on demand) to refresh the GitHub Pages dashboard.
Pure reader — never mutates trading state.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MEMORY_DIR = _PROJECT_ROOT / "memory"
_KB_DIR = _PROJECT_ROOT / "kb"
_DASHBOARD_DIR = _PROJECT_ROOT / "docs" / "dashboard"
_DATA_PATH = _DASHBOARD_DIR / "data.json"


# ---------------------------------------------------------------------------
# Readers — each returns a dict fragment for the dashboard JSON
# ---------------------------------------------------------------------------

def _read_portfolio_state() -> dict[str, Any]:
    """Parse machine-readable state from PROJECT-CONTEXT.md."""
    ctx = _MEMORY_DIR / "PROJECT-CONTEXT.md"
    defaults = {
        "peak_equity": 0.0,
        "circuit_breaker_triggered": False,
        "current_mode": "unknown",
        "weekly_trade_count": 0,
    }
    if not ctx.exists():
        return defaults

    text = ctx.read_text(encoding="utf-8")
    patterns = {
        "peak_equity": (r"peak_equity\s*[:=]\s*([\d.]+)", float),
        "circuit_breaker_triggered": (r"circuit_breaker_triggered\s*[:=]\s*(\w+)", lambda v: v.lower() == "true"),
        "current_mode": (r"current_mode\s*[:=]\s*(\w+)", str),
        "weekly_trade_count": (r"weekly_trade_count\s*[:=]\s*(\d+)", int),
    }
    result = dict(defaults)
    for key, (pattern, cast) in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                result[key] = cast(m.group(1).strip())
            except (ValueError, TypeError):
                pass
    return result


def _read_equity_history() -> list[dict[str, Any]]:
    """Parse EOD snapshots from TRADE-LOG.md for the equity curve."""
    log_path = _MEMORY_DIR / "TRADE-LOG.md"
    if not log_path.exists():
        return []

    text = log_path.read_text(encoding="utf-8")
    # Match: ### YYYY-MM-DD — EOD Snapshot
    # **Portfolio:** $100000.00 | **Cash:** $100000.00 | **Day P&L:** +0.00
    pattern = re.compile(
        r"###\s+(\d{4}-\d{2}-\d{2})\s+.+?EOD Snapshot\s*\n"
        r"\*\*Portfolio:\*\*\s+\$([0-9,.]+)\s*\|\s*"
        r"\*\*Cash:\*\*\s+\$([0-9,.]+)\s*\|\s*"
        r"\*\*Day P&L:\*\*\s+([+-]?[0-9,.]+)",
        re.MULTILINE,
    )
    seen_dates: set[str] = set()
    points: list[dict[str, Any]] = []
    for m in pattern.finditer(text):
        dt = m.group(1)
        if dt in seen_dates:
            continue  # deduplicate duplicate EOD writes
        seen_dates.add(dt)
        points.append({
            "date": dt,
            "equity": float(m.group(2).replace(",", "")),
            "cash": float(m.group(3).replace(",", "")),
            "day_pnl": float(m.group(4).replace(",", "")),
        })
    return points


def _read_open_trades() -> dict[str, dict[str, Any]]:
    """Read open-trades.json sidecar."""
    path = _MEMORY_DIR / "open-trades.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_closed_trades(limit: int = 50) -> list[dict[str, Any]]:
    """Read most recent closed trades from kb/trades/."""
    trades_dir = _KB_DIR / "trades"
    if not trades_dir.exists():
        return []
    files = sorted(trades_dir.glob("*.json"), reverse=True)[:limit]
    out: list[dict[str, Any]] = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _read_daily_snapshots(limit: int = 90) -> list[dict[str, Any]]:
    """Read recent daily snapshots from kb/daily/."""
    daily_dir = _KB_DIR / "daily"
    if not daily_dir.exists():
        return []
    files = sorted(daily_dir.glob("*.json"), reverse=True)[:limit]
    out: list[dict[str, Any]] = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return list(reversed(out))  # chronological order


def _read_kill_switch() -> dict[str, Any]:
    """Check if kill switch is active."""
    flag = _MEMORY_DIR / "KILL.flag"
    if flag.exists():
        try:
            reason = flag.read_text(encoding="utf-8").strip()
        except OSError:
            reason = "(could not read)"
        return {"active": True, "reason": reason}
    return {"active": False, "reason": ""}


def _read_push_failed() -> bool:
    return (_MEMORY_DIR / "PUSH-FAILED.flag").exists()


def _compute_stats(equity_history: list[dict], closed_trades: list[dict]) -> dict[str, Any]:
    """Compute aggregate performance stats."""
    stats: dict[str, Any] = {
        "total_trades": len(closed_trades),
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "avg_r_multiple": 0.0,
        "max_drawdown_pct": 0.0,
        "current_drawdown_pct": 0.0,
    }

    # Trade stats
    pnls: list[float] = []
    r_multiples: list[float] = []
    for t in closed_trades:
        pnl = float(t.get("realized_pnl", t.get("pnl", 0.0)))
        pnls.append(pnl)
        if pnl > 0:
            stats["wins"] += 1
        elif pnl < 0:
            stats["losses"] += 1

        r_mult = t.get("r_multiple")
        if r_mult is not None:
            r_multiples.append(float(r_mult))

    stats["total_pnl"] = round(sum(pnls), 2)
    if pnls:
        stats["best_trade"] = round(max(pnls), 2)
        stats["worst_trade"] = round(min(pnls), 2)
    if stats["total_trades"] > 0:
        stats["win_rate"] = round(stats["wins"] / stats["total_trades"] * 100, 1)
    if r_multiples:
        stats["avg_r_multiple"] = round(sum(r_multiples) / len(r_multiples), 2)

    # Drawdown from equity curve
    if equity_history:
        peak = 0.0
        max_dd = 0.0
        for pt in equity_history:
            eq = pt["equity"]
            peak = max(peak, eq)
            if peak > 0:
                dd = (peak - eq) / peak * 100
                max_dd = max(max_dd, dd)
        stats["max_drawdown_pct"] = round(max_dd, 2)

        current_eq = equity_history[-1]["equity"]
        if peak > 0:
            stats["current_drawdown_pct"] = round((peak - current_eq) / peak * 100, 2)

    return stats


def _read_handoff() -> dict[str, str]:
    """Read today's handoff sections."""
    path = _MEMORY_DIR / "DAILY-HANDOFF.md"
    if not path.exists():
        return {}
    try:
        return {"raw": path.read_text(encoding="utf-8")}
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_dashboard_data() -> Path:
    """
    Collect all data sources and write docs/dashboard/data.json.

    Returns the path to the written file.
    """
    equity_history = _read_equity_history()
    closed_trades = _read_closed_trades()

    data = {
        "generated_at": datetime.now().isoformat(),
        "state": _read_portfolio_state(),
        "kill_switch": _read_kill_switch(),
        "push_failed": _read_push_failed(),
        "equity_history": equity_history,
        "daily_snapshots": _read_daily_snapshots(),
        "open_trades": _read_open_trades(),
        "closed_trades": closed_trades,
        "stats": _compute_stats(equity_history, closed_trades),
        "handoff": _read_handoff(),
    }

    _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    _DATA_PATH.write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Dashboard data written to %s", _DATA_PATH)
    return _DATA_PATH
