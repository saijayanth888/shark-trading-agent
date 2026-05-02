"""
Outcome Resolver — deferred reflection on closed trades.

Inspired by TradingAgents' Phase B deferred reflection. For each closed trade:
  1. Fetch actual returns (raw + alpha vs SPY)
  2. Generate a structured LLM reflection on what held/failed
  3. Append to LESSONS-LEARNED.md with outcome data
  4. Feed lessons into future analyst prompts automatically

Hooked into the daily-summary phase so it runs every EOD.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from shark.config import get_settings

logger = logging.getLogger(__name__)

try:
    import anthropic as _anthropic_lib
except ImportError:
    _anthropic_lib = None

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LESSONS_FILE = _PROJECT_ROOT / "memory" / "LESSONS-LEARNED.md"
_TRADE_LOG = _PROJECT_ROOT / "memory" / "TRADE-LOG.md"
_PENDING_FILE = _PROJECT_ROOT / "memory" / "pending-outcomes.json"


# ---------------------------------------------------------------------------
# Return fetcher — uses yfinance for historical data
# ---------------------------------------------------------------------------

def _fetch_returns(
    symbol: str,
    entry_date: str,
    exit_date: str,
    entry_price: float,
    exit_price: float,
) -> dict[str, Any]:
    """
    Compute raw return and alpha vs SPY for a closed trade.

    Args:
        symbol: Ticker that was traded.
        entry_date: ISO date of entry.
        exit_date: ISO date of exit.
        entry_price: Entry price.
        exit_price: Exit price.

    Returns:
        Dict with raw_return_pct, alpha_vs_spy_pct, holding_days, spy_return_pct.
    """
    raw_return = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

    # Try to get SPY return over same period for alpha calculation
    spy_return = 0.0
    holding_days = 0

    try:
        start = datetime.strptime(entry_date, "%Y-%m-%d")
        end = datetime.strptime(exit_date, "%Y-%m-%d")
        holding_days = (end - start).days

        try:
            import yfinance as yf
            spy_data = yf.Ticker("SPY").history(
                start=entry_date,
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            )
            if len(spy_data) >= 2:
                spy_start = float(spy_data["Close"].iloc[0])
                spy_end = float(spy_data["Close"].iloc[-1])
                spy_return = ((spy_end - spy_start) / spy_start) * 100.0
        except ImportError:
            logger.debug("yfinance not available — alpha calculation skipped")
        except Exception as exc:
            logger.debug("SPY data fetch failed: %s", exc)

    except (ValueError, TypeError):
        pass

    alpha = raw_return - spy_return

    return {
        "raw_return_pct": round(raw_return, 2),
        "spy_return_pct": round(spy_return, 2),
        "alpha_vs_spy_pct": round(alpha, 2),
        "holding_days": holding_days,
    }


# ---------------------------------------------------------------------------
# LLM Reflection
# ---------------------------------------------------------------------------

_REFLECTION_SYSTEM = (
    "You are a trading analyst reviewing your own past decision now that the outcome is known. "
    "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown). "
    "Cover in order: "
    "1. Was the directional call correct? (cite the alpha figure) "
    "2. Which part of the investment thesis held or failed? "
    "3. One concrete lesson to apply to the next similar analysis. "
    "Be specific and terse. Your output will be stored verbatim in a decision log "
    "and re-read by future analysts, so every word must earn its place."
)


def _generate_reflection(
    trade: dict[str, Any],
    returns: dict[str, Any],
) -> str:
    """Generate an LLM reflection on the trade outcome. Falls back to template."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or _anthropic_lib is None:
        return _template_reflection(trade, returns)

    try:
        client = _anthropic_lib.Anthropic(api_key=api_key)
        raw_ret = returns["raw_return_pct"]
        alpha = returns["alpha_vs_spy_pct"]

        prompt = (
            f"Symbol: {trade.get('symbol', '???')}\n"
            f"Entry: ${trade.get('entry_price', 0):.2f} on {trade.get('entry_date', '?')}\n"
            f"Exit: ${trade.get('exit_price', 0):.2f} on {trade.get('exit_date', '?')}\n"
            f"Exit reason: {trade.get('exit_reason', 'unknown')}\n"
            f"Raw return: {raw_ret:+.1f}%\n"
            f"Alpha vs SPY: {alpha:+.1f}%\n"
            f"Catalyst: {trade.get('catalyst', 'N/A')}\n"
            f"Thesis: {trade.get('thesis_summary', 'N/A')}\n"
        )

        cfg = get_settings()
        response = client.messages.create(
            model=cfg.claude_model,
            max_tokens=300,
            temperature=0.3,
            system=[{"type": "text", "text": _REFLECTION_SYSTEM}],
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    except Exception as exc:
        logger.warning("LLM reflection failed: %s — using template", exc)
        return _template_reflection(trade, returns)


def _template_reflection(trade: dict, returns: dict) -> str:
    """Deterministic fallback reflection when LLM is unavailable."""
    raw = returns["raw_return_pct"]
    alpha = returns["alpha_vs_spy_pct"]
    direction = "correct" if raw > 0 else "wrong"
    exit_reason = trade.get("exit_reason", "unknown")

    return (
        f"Directional call was {direction} with {raw:+.1f}% raw return "
        f"({alpha:+.1f}% alpha vs SPY). "
        f"Exit via {exit_reason}. "
        f"Lesson: {'Tighten entries on low-alpha trades' if alpha < 0 else 'Similar setups worth repeating'}."
    )


# ---------------------------------------------------------------------------
# Pending outcomes tracker
# ---------------------------------------------------------------------------

def store_pending_outcome(
    symbol: str,
    entry_date: str,
    entry_price: float,
    trade_decision: dict[str, Any],
) -> None:
    """Store a trade entry for deferred outcome resolution."""
    pending = _load_pending()
    pending.append({
        "symbol": symbol,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "thesis_summary": trade_decision.get("thesis_summary", ""),
        "catalyst": trade_decision.get("bull_thesis", trade_decision.get("catalyst", "")),
        "confidence": trade_decision.get("confidence", 0.0),
        "stored_at": datetime.now().isoformat(),
    })
    _save_pending(pending)
    logger.info("Stored pending outcome for %s (entry %s @ $%.2f)", symbol, entry_date, entry_price)


def resolve_closed_trades(closed_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Resolve closed trades against actual outcomes and generate reflections.

    Args:
        closed_trades: List of dicts with keys:
            symbol, entry_date, exit_date, entry_price, exit_price,
            exit_reason, pnl_pct, catalyst, thesis_summary

    Returns:
        List of reflection results, each with:
            symbol, returns, reflection_text, appended_to_lessons
    """
    results = []

    for trade in closed_trades:
        symbol = trade.get("symbol", "???")
        entry_date = trade.get("entry_date", "")
        exit_date = trade.get("exit_date", "")
        entry_price = float(trade.get("entry_price", 0))
        exit_price = float(trade.get("exit_price", 0))

        if not entry_date or not exit_date or entry_price <= 0:
            logger.debug("Skipping incomplete trade: %s", trade)
            continue

        # Compute returns
        returns = _fetch_returns(symbol, entry_date, exit_date, entry_price, exit_price)

        # Generate reflection
        reflection = _generate_reflection(trade, returns)

        # Append to LESSONS-LEARNED.md
        _append_outcome_lesson(trade, returns, reflection)

        # Remove from pending
        _remove_from_pending(symbol, entry_date)

        results.append({
            "symbol": symbol,
            "returns": returns,
            "reflection_text": reflection,
            "appended_to_lessons": True,
        })

        logger.info(
            "Resolved outcome: %s raw=%+.1f%% alpha=%+.1f%% — %s",
            symbol, returns["raw_return_pct"], returns["alpha_vs_spy_pct"],
            reflection[:80],
        )

    return results


def _append_outcome_lesson(
    trade: dict, returns: dict, reflection: str,
) -> None:
    """Append an outcome-based lesson to LESSONS-LEARNED.md."""
    _LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not _LESSONS_FILE.exists():
        _LESSONS_FILE.write_text(
            "# Shark Trading Agent — Lessons Learned\n\n", encoding="utf-8"
        )

    text = _LESSONS_FILE.read_text(encoding="utf-8")
    if "| Date |" not in text:
        header = (
            "| Date | Symbol | Grade | P&L | Pattern | Lesson |\n"
            "|------|--------|-------|-----|---------|--------|\n"
        )
        text += header
        _LESSONS_FILE.write_text(text, encoding="utf-8")

    raw = returns["raw_return_pct"]
    alpha = returns["alpha_vs_spy_pct"]

    # Grade based on alpha
    if alpha > 5:
        grade = "A"
    elif alpha > 2:
        grade = "B"
    elif alpha > -2:
        grade = "C"
    elif alpha > -5:
        grade = "D"
    else:
        grade = "F"

    exit_reason = trade.get("exit_reason", "unknown")
    symbol = trade.get("symbol", "???")
    exit_date = trade.get("exit_date", datetime.now().strftime("%Y-%m-%d"))

    # Truncate reflection to fit table
    lesson_short = reflection[:120].replace("|", "/").replace("\n", " ")

    entry = (
        f"| {exit_date} | {symbol} | {grade} | {raw:+.1f}% (α{alpha:+.1f}%) | "
        f"{exit_reason} | {lesson_short} |\n"
    )

    with _LESSONS_FILE.open("a", encoding="utf-8") as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# Pending file I/O
# ---------------------------------------------------------------------------

def get_pending_outcomes() -> list[dict]:
    """Return all pending outcomes (public API for cross-module use)."""
    return _load_pending()


def _load_pending() -> list[dict]:
    """Load pending outcomes from disk."""
    if not _PENDING_FILE.exists():
        return []
    try:
        return json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_pending(pending: list[dict]) -> None:
    """Save pending outcomes to disk."""
    _PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PENDING_FILE.write_text(
        json.dumps(pending, indent=2, default=str), encoding="utf-8"
    )


def _remove_from_pending(symbol: str, entry_date: str) -> None:
    """Remove a resolved trade from the pending list."""
    pending = _load_pending()
    pending = [
        p for p in pending
        if not (p.get("symbol") == symbol and p.get("entry_date") == entry_date)
    ]
    _save_pending(pending)
