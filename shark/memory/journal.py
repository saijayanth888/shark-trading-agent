"""
Trade & Research Journal — append-only markdown logging for all agent activity.

All files are stored under memory/ relative to the project root.
Files are created with headers if they do not exist.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Resolve memory dir relative to this file's project root
# Project structure: shark-trading-agent/shark/memory/journal.py
#                    shark-trading-agent/memory/
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # shark-trading-agent/
_MEMORY_DIR = _PROJECT_ROOT / "memory"

_TRADE_LOG_FILE = _MEMORY_DIR / "TRADE-LOG.md"
_RESEARCH_LOG_FILE = _MEMORY_DIR / "RESEARCH-LOG.md"
_WEEKLY_REVIEW_FILE = _MEMORY_DIR / "WEEKLY-REVIEW.md"

_TRADE_LOG_HEADER = (
    "# Shark Trading Agent — Trade Log\n\n"
    "| Date | Symbol | Action | Qty | Price | Stop | Target | R:R | Thesis | Status |\n"
    "|------|--------|--------|-----|-------|------|--------|-----|--------|--------|\n"
)

_RESEARCH_LOG_HEADER = "# Shark Trading Agent — Research Log\n\n"
_WEEKLY_REVIEW_HEADER = "# Shark Trading Agent — Weekly Reviews\n\n"


def _ensure_dir() -> None:
    """Create memory/ directory if it does not exist."""
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_file(path: Path, header: str) -> None:
    """Create a markdown file with header if it does not already exist."""
    _ensure_dir()
    if not path.exists():
        path.write_text(header, encoding="utf-8")
        logger.info("Created %s", path)


def _coerce_float(value: Any) -> float:
    """Tolerant float coercion — '-' / '' / None all map to 0.0 (display-only)."""
    if value in (None, "", "-"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_optional(value: Any, fmt: str = "{:.2f}") -> str:
    """Format a number, or pass through '-' / '' for display."""
    if value in (None, "", "-"):
        return "-"
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def log_trade(trade_data: dict[str, Any]) -> None:
    """
    Append a single trade entry as a markdown table row to TRADE-LOG.md.

    Accepted keys (alias-tolerant — both old and new schemas work):
        date         (optional, defaults to today)
        symbol       required
        action | side          buy / sell / SELL (...)
        qty
        price
        stop
        target       optional take-profit
        rr | risk_reward_ratio optional R:R
        thesis | catalyst      free-text reason
        status       optional, defaults to OPEN
    """
    _ensure_file(_TRADE_LOG_FILE, _TRADE_LOG_HEADER)

    date = trade_data.get("date") or datetime.now().strftime("%Y-%m-%d")
    symbol = trade_data.get("symbol", "")
    # Alias tolerance — callers historically use "side", journal originally read "action"
    action = trade_data.get("action") or trade_data.get("side") or ""
    qty = trade_data.get("qty", "")
    price = _coerce_float(trade_data.get("price"))
    stop_display = _format_optional(trade_data.get("stop"))
    target_display = _format_optional(trade_data.get("target"))
    rr_display = _format_optional(
        trade_data.get("rr") or trade_data.get("risk_reward_ratio"),
        fmt="{:.2f}",
    )
    # thesis | catalyst alias
    thesis_raw = trade_data.get("thesis") or trade_data.get("catalyst") or ""
    thesis = str(thesis_raw)[:50].replace("|", "/")  # don't break the markdown table
    status = trade_data.get("status", "OPEN")

    row = (
        f"| {date} | {symbol} | {action} | {qty} | "
        f"{price:.2f} | {stop_display} | {target_display} | {rr_display} | "
        f"{thesis} | {status} |\n"
    )

    with _TRADE_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(row)

    logger.info("Trade logged: %s %s x%s @ %.2f", action, symbol, qty, price)


def log_research(research_data: dict[str, Any]) -> None:
    """
    Append a dated research section to RESEARCH-LOG.md.

    Args:
        research_data: Dict with keys:
            date (str, optional), symbol (str),
            sentiment_score (float), thesis (str),
            entry (float), stop (float), target (float).
    """
    _ensure_file(_RESEARCH_LOG_FILE, _RESEARCH_LOG_HEADER)

    date = research_data.get("date") or datetime.now().strftime("%Y-%m-%d")
    symbol = research_data.get("symbol", "")
    score = research_data.get("sentiment_score", research_data.get("score", 0.0))
    thesis = research_data.get("thesis", "")
    entry = float(research_data.get("entry", research_data.get("entry_price", 0.0)))
    stop = float(research_data.get("stop", research_data.get("stop_loss", 0.0)))
    target = float(research_data.get("target", research_data.get("target_price", 0.0)))

    section = (
        f"## {date} — {symbol}\n"
        f"**Sentiment:** {score}\n"
        f"**Thesis:** {thesis}\n"
        f"**Entry:** {entry} | **Stop:** {stop} | **Target:** {target}\n\n"
    )

    with _RESEARCH_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(section)

    logger.info("Research logged for %s on %s", symbol, date)


def write_daily_summary(summary: dict[str, Any]) -> None:
    """
    Append an end-of-day snapshot section to TRADE-LOG.md.

    Args:
        summary: Dict with keys:
            date (str, optional), equity (float), cash (float),
            day_pl (float), open_positions (int, optional),
            notes (str, optional).
    """
    _ensure_file(_TRADE_LOG_FILE, _TRADE_LOG_HEADER)

    date = summary.get("date") or datetime.now().strftime("%Y-%m-%d")
    equity = float(summary.get("equity", 0.0))
    cash = float(summary.get("cash", 0.0))
    day_pl = float(summary.get("day_pl", 0.0))
    open_positions = summary.get("open_positions", "N/A")
    notes = summary.get("notes", "")

    sign = "+" if day_pl >= 0 else ""

    section = (
        f"\n### {date} — EOD Snapshot\n"
        f"**Portfolio:** ${equity:.2f} | "
        f"**Cash:** ${cash:.2f} | "
        f"**Day P&L:** {sign}{day_pl:.2f} | "
        f"**Open Positions:** {open_positions}\n"
    )

    if notes:
        section += f"**Notes:** {notes}\n"

    section += "\n"

    with _TRADE_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(section)

    logger.info("Daily summary written for %s", date)


def write_weekly_review(review: dict[str, Any]) -> None:
    """
    Append a weekly review section to WEEKLY-REVIEW.md.

    Args:
        review: Dict with keys:
            week_label (str), total_trades (int), wins (int),
            losses (int), total_pl (float), win_rate (float),
            what_worked (str), what_didnt (str), grade (str),
            next_week_focus (str, optional).
    """
    _ensure_file(_WEEKLY_REVIEW_FILE, _WEEKLY_REVIEW_HEADER)

    week_label = review.get("week_label", datetime.now().strftime("Week of %Y-%m-%d"))
    total_trades = int(review.get("total_trades", 0))
    wins = int(review.get("wins", 0))
    losses = int(review.get("losses", 0))
    total_pl = float(review.get("total_pl", 0.0))
    win_rate = float(review.get("win_rate", 0.0))
    what_worked = review.get("what_worked", "")
    what_didnt = review.get("what_didnt", "")
    grade = review.get("grade", "N/A")
    next_week_focus = review.get("next_week_focus", "")

    pl_sign = "+" if total_pl >= 0 else ""

    section = (
        f"## {week_label}\n\n"
        f"**Grade: {grade}**\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Total Trades | {total_trades} |\n"
        f"| Wins | {wins} |\n"
        f"| Losses | {losses} |\n"
        f"| Win Rate | {win_rate:.1%} |\n"
        f"| Total P&L | {pl_sign}${abs(total_pl):.2f} |\n\n"
        f"**What Worked:**\n{what_worked}\n\n"
        f"**What Didn't:**\n{what_didnt}\n\n"
    )

    if next_week_focus:
        section += f"**Next Week Focus:**\n{next_week_focus}\n\n"

    section += "---\n\n"

    with _WEEKLY_REVIEW_FILE.open("a", encoding="utf-8") as f:
        f.write(section)

    logger.info("Weekly review written for %s (grade: %s)", week_label, grade)
