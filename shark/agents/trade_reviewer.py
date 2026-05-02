"""
AI Trade Reviewer — post-trade analysis that feeds lessons back into the system.

After every closed trade:
  1. Analyze what went right or wrong
  2. Classify the trade pattern (momentum continuation, failed breakout, etc.)
  3. Extract a one-line lesson
  4. Store in memory/LESSONS-LEARNED.md
  5. Feed top lessons into the combined_analyst system prompt

This creates an adaptive learning loop — the agent gets smarter over time.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from shark.config import get_settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LESSONS_FILE = _PROJECT_ROOT / "memory" / "LESSONS-LEARNED.md"
_LESSONS_HEADER = "# Shark Trading Agent — Lessons Learned\n\n"

try:
    import anthropic as _anthropic_lib
except ImportError:
    _anthropic_lib = None

_REVIEW_SYSTEM_PROMPT = (
    "You are a trading performance coach analyzing a completed trade. "
    "Be brutally honest about what worked and what didn't. "
    "Focus on actionable lessons, not hindsight bias. "
    "Return ONLY valid JSON."
)


def review_closed_trade(
    trade: dict[str, Any],
    market_context: str = "",
) -> dict[str, Any]:
    """
    Analyze a closed trade and extract lessons.

    Args:
        trade: Dict with keys:
            symbol, side, entry_price, exit_price, qty, entry_date,
            exit_date, pnl_dollars, pnl_pct, catalyst, stop_price,
            exit_reason (e.g., "stop-out", "target", "thesis-break", "time-decay")
        market_context: Optional string describing market conditions during the trade

    Returns:
        Dict with:
            grade (str): A-F grade for this trade
            pattern (str): Classified pattern
            lesson (str): One-line actionable lesson
            analysis (str): Detailed analysis
            what_worked (str): What was done well
            what_failed (str): What went wrong
    """
    pnl_pct = float(trade.get("pnl_pct", 0.0))
    exit_reason = trade.get("exit_reason", "unknown")

    # Try AI review first, fall back to rule-based
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key and _anthropic_lib is not None:
        try:
            return _ai_review(trade, market_context)
        except Exception as exc:
            logger.warning("AI review failed, using rule-based: %s", exc)

    return _rule_based_review(trade)


def _ai_review(trade: dict[str, Any], market_context: str) -> dict[str, Any]:
    """Use Claude to analyze the trade."""
    client = _anthropic_lib.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Analyze this completed trade:

```json
{json.dumps(trade, indent=2, default=str)}
```

Market context: {market_context or "Not provided"}

Return JSON:
{{
  "grade": "<A/B/C/D/F>",
  "pattern": "<e.g. momentum_continuation, failed_breakout, thesis_decay, stop_hunt, trend_reversal>",
  "lesson": "<one actionable sentence that improves future trading>",
  "analysis": "<2-3 sentence detailed analysis>",
  "what_worked": "<what was done well>",
  "what_failed": "<what went wrong>"
}}

Grading: A=great execution, B=good but improvable, C=mediocre, D=poor execution, F=rule violation"""

    cfg = get_settings()
    response = client.messages.create(
        model=cfg.claude_model,
        max_tokens=600,
        temperature=0.3,
        system=[{"type": "text", "text": _REVIEW_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()

    result = json.loads(raw)
    result.setdefault("grade", "C")
    result.setdefault("pattern", "unknown")
    result.setdefault("lesson", "Review trade manually")

    return result


def _rule_based_review(trade: dict[str, Any]) -> dict[str, Any]:
    """Deterministic trade review when AI is unavailable."""
    pnl_pct = float(trade.get("pnl_pct", 0.0))
    exit_reason = trade.get("exit_reason", "unknown")

    # Grade based on outcome and exit quality
    if pnl_pct >= 10:
        grade = "A"
        what_worked = "Strong profit target reached"
        what_failed = "None — good execution"
    elif pnl_pct >= 5:
        grade = "B"
        what_worked = "Solid profit captured"
        what_failed = "Could potentially have run longer"
    elif pnl_pct >= 0:
        grade = "C"
        what_worked = "Avoided a loss"
        what_failed = "Thesis didn't play out fully"
    elif pnl_pct >= -3:
        grade = "C"
        what_worked = "Cut loss early"
        what_failed = "Entry timing needs improvement"
    elif pnl_pct >= -7:
        grade = "D"
        what_worked = "Hard stop prevented larger loss"
        what_failed = "Poor entry or thesis was wrong"
    else:
        grade = "F"
        what_worked = "Nothing — review entry criteria"
        what_failed = "Large loss — possible rule violation"

    # Pattern classification
    if exit_reason == "target":
        pattern = "momentum_continuation"
    elif exit_reason == "stop-out" and pnl_pct < -5:
        pattern = "failed_breakout"
    elif exit_reason == "thesis-break":
        pattern = "thesis_invalidation"
    elif exit_reason == "time-decay":
        pattern = "thesis_decay"
    else:
        pattern = "unknown"

    # Generate lesson
    lessons = {
        "momentum_continuation": "Entry timing was good — continue scanning for similar setups",
        "failed_breakout": "Tighten entry criteria — require stronger volume confirmation before entry",
        "thesis_invalidation": "Improve pre-trade research — check for upcoming headwinds more thoroughly",
        "thesis_decay": "Set stricter time limits — if thesis hasn't played out in 3 days, reduce position",
        "unknown": "Document this trade pattern for future reference",
    }

    return {
        "grade": grade,
        "pattern": pattern,
        "lesson": lessons.get(pattern, "Review and improve"),
        "analysis": f"Trade closed via {exit_reason} with {pnl_pct:+.1f}% return",
        "what_worked": what_worked,
        "what_failed": what_failed,
    }


def save_lesson(
    trade: dict[str, Any],
    review: dict[str, Any],
) -> None:
    """
    Append a lesson to memory/LESSONS-LEARNED.md.

    Format designed for easy parsing by the combined_analyst system prompt.
    """
    _LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _LESSONS_FILE.exists():
        _LESSONS_FILE.write_text(_LESSONS_HEADER, encoding="utf-8")

    symbol = trade.get("symbol", "???")
    entry_date = trade.get("entry_date", "")
    exit_date = trade.get("exit_date", datetime.now().strftime("%Y-%m-%d"))
    pnl_pct = float(trade.get("pnl_pct", 0.0))
    grade = review.get("grade", "?")
    pattern = review.get("pattern", "unknown")
    lesson = review.get("lesson", "")

    entry = (
        f"| {exit_date} | {symbol} | {grade} | {pnl_pct:+.1f}% | "
        f"{pattern} | {lesson} |\n"
    )

    # Add table header if first entry
    text = _LESSONS_FILE.read_text(encoding="utf-8")
    if "| Date |" not in text:
        header = (
            "| Date | Symbol | Grade | P&L | Pattern | Lesson |\n"
            "|------|--------|-------|-----|---------|--------|\n"
        )
        text += header
        _LESSONS_FILE.write_text(text, encoding="utf-8")

    with _LESSONS_FILE.open("a", encoding="utf-8") as f:
        f.write(entry)

    logger.info("Lesson saved: %s %s grade=%s pnl=%+.1f%%", symbol, pattern, grade, pnl_pct)


def get_recent_lessons(n: int = 10) -> list[str]:
    """
    Return the N most recent one-line lessons for injection into analyst prompts.

    Returns:
        List of lesson strings (most recent first).
    """
    if not _LESSONS_FILE.exists():
        return []

    try:
        text = _LESSONS_FILE.read_text(encoding="utf-8")
    except Exception:
        return []

    # Parse table rows: | Date | Symbol | Grade | P&L | Pattern | Lesson |
    pattern = re.compile(
        r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|.*?\|\s*(.*?)\s*\|$",
        re.MULTILINE,
    )

    lessons = [m.group(1).strip() for m in pattern.finditer(text) if m.group(1).strip()]

    # Return most recent N
    return lessons[-n:][::-1]


def get_pattern_stats() -> dict[str, dict[str, Any]]:
    """
    Compute win rates by trade pattern for strategy adaptation.

    Returns:
        Dict of pattern → {count, wins, losses, win_rate, avg_pnl}
    """
    if not _LESSONS_FILE.exists():
        return {}

    try:
        text = _LESSONS_FILE.read_text(encoding="utf-8")
    except Exception:
        return {}

    stats: dict[str, dict[str, Any]] = {}

    # Parse: | Date | Symbol | Grade | P&L | Pattern | Lesson |
    row_pattern = re.compile(
        r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|\s*(\w+)\s*\|\s*(\w)\s*\|\s*([+-]?[\d.]+)%\s*\|\s*(\w+)\s*\|",
        re.MULTILINE,
    )

    for m in row_pattern.finditer(text):
        pnl = float(m.group(3))
        pattern_name = m.group(4)

        if pattern_name not in stats:
            stats[pattern_name] = {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}

        stats[pattern_name]["count"] += 1
        stats[pattern_name]["total_pnl"] += pnl
        if pnl > 0:
            stats[pattern_name]["wins"] += 1
        else:
            stats[pattern_name]["losses"] += 1

    # Compute derived metrics
    for p in stats.values():
        p["win_rate"] = round(p["wins"] / p["count"] * 100, 1) if p["count"] > 0 else 0.0
        p["avg_pnl"] = round(p["total_pnl"] / p["count"], 2) if p["count"] > 0 else 0.0

    return stats
