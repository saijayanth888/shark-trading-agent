"""
Portfolio State — reads and writes agent state to/from PROJECT-CONTEXT.md.

Tracks peak equity, circuit-breaker status, trading mode, and weekly trade counts.
Uses subprocess for git operations.
"""

import logging
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # shark-trading-agent/
_MEMORY_DIR = _PROJECT_ROOT / "memory"
_CONTEXT_FILE = _MEMORY_DIR / "PROJECT-CONTEXT.md"
_TRADE_LOG_FILE = _MEMORY_DIR / "TRADE-LOG.md"

# Default state values
_DEFAULTS: dict[str, Any] = {
    "start_date": "",
    "initial_capital": 0.0,
    "peak_equity": 0.0,
    "current_mode": "paper",
    "circuit_breaker_triggered": False,
}


# ---------------------------------------------------------------------------
# State reader
# ---------------------------------------------------------------------------

def get_portfolio_state() -> dict[str, Any]:
    """
    Read current agent state from memory/PROJECT-CONTEXT.md.

    Parses simple key: value markdown lines. Falls back to defaults if the
    file does not exist or a key is missing.

    Returns:
        Dict with keys: start_date, initial_capital, peak_equity,
        current_mode, circuit_breaker_triggered.
    """
    state = dict(_DEFAULTS)

    if not _CONTEXT_FILE.exists():
        logger.warning("PROJECT-CONTEXT.md not found; returning default state.")
        return state

    try:
        text = _CONTEXT_FILE.read_text(encoding="utf-8")

        patterns = {
            "start_date": r"start_date\s*[:=]\s*(.+)",
            "initial_capital": r"initial_capital\s*[:=]\s*([\d.]+)",
            "peak_equity": r"peak_equity\s*[:=]\s*([\d.]+)",
            "current_mode": r"current_mode\s*[:=]\s*(\w+)",
            "circuit_breaker_triggered": r"circuit_breaker_triggered\s*[:=]\s*(true|false)",
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
                if key in ("initial_capital", "peak_equity"):
                    state[key] = float(raw)
                elif key == "circuit_breaker_triggered":
                    state[key] = raw.lower() == "true"
                else:
                    state[key] = raw

    except Exception as exc:
        logger.error("Error reading PROJECT-CONTEXT.md: %s", exc)

    return state


# ---------------------------------------------------------------------------
# Peak equity updater
# ---------------------------------------------------------------------------

def update_peak_equity(new_equity: float) -> None:
    """
    Update peak_equity in PROJECT-CONTEXT.md if new_equity exceeds the current peak.

    If the file does not exist, creates it with a minimal template.

    Args:
        new_equity: The current portfolio value to compare against the stored peak.
    """
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    current_state = get_portfolio_state()
    current_peak = float(current_state.get("peak_equity", 0.0))

    if new_equity <= current_peak:
        logger.debug(
            "Peak equity unchanged: %.2f <= %.2f", new_equity, current_peak
        )
        return

    logger.info(
        "New peak equity: %.2f (was %.2f)", new_equity, current_peak
    )

    if not _CONTEXT_FILE.exists():
        # Bootstrap a minimal context file
        content = (
            "# Shark Trading Agent — Project Context\n\n"
            f"start_date: {datetime.now().strftime('%Y-%m-%d')}\n"
            "initial_capital: 0.0\n"
            f"peak_equity: {new_equity:.2f}\n"
            "current_mode: paper\n"
            "circuit_breaker_triggered: false\n"
        )
        _CONTEXT_FILE.write_text(content, encoding="utf-8")
        return

    text = _CONTEXT_FILE.read_text(encoding="utf-8")

    # Replace existing peak_equity line
    updated = re.sub(
        r"(peak_equity\s*[:=]\s*)[\d.]+",
        lambda m: f"{m.group(1)}{new_equity:.2f}",
        text,
        flags=re.IGNORECASE,
    )

    # If line was not found, append it
    if updated == text:
        updated = text.rstrip() + f"\npeak_equity: {new_equity:.2f}\n"

    _CONTEXT_FILE.write_text(updated, encoding="utf-8")
    logger.info("peak_equity updated to %.2f in PROJECT-CONTEXT.md", new_equity)


# ---------------------------------------------------------------------------
# Git memory commit
# ---------------------------------------------------------------------------

def commit_memory(message: str) -> bool:
    """
    Stage all files in memory/ and create a git commit.

    Args:
        message: Commit message.

    Returns:
        True if the commit succeeded, False otherwise.
    """
    try:
        # Stage memory directory
        add_result = subprocess.run(
            ["git", "add", "memory/"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if add_result.returncode != 0:
            logger.error("git add failed: %s", add_result.stderr)
            return False

        # Check if there is anything to commit
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "memory/"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if not status_result.stdout.strip():
            logger.info("No changes in memory/ to commit.")
            return True

        commit_result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if commit_result.returncode != 0:
            logger.error("git commit failed: %s", commit_result.stderr)
            return False

        logger.info("Memory committed: %s", message)

        # Push to remote — required for cloud routines (ephemeral containers)
        push_result = subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )

        if push_result.returncode != 0:
            # Try rebase pull then push once more
            subprocess.run(
                ["git", "pull", "--rebase", "origin", "main"],
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            retry = subprocess.run(
                ["git", "push", "origin", "HEAD:main"],
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if retry.returncode != 0:
                logger.error("git push failed after rebase: %s", retry.stderr)
                return False

        logger.info("Memory pushed to origin/main")
        return True

    except subprocess.TimeoutExpired:
        logger.error("git operation timed out.")
        return False

    except Exception as exc:
        logger.error("Unexpected error during git commit/push: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Circuit breaker control
# ---------------------------------------------------------------------------

def set_circuit_breaker_triggered(triggered: bool) -> None:
    """
    Write circuit_breaker_triggered: true/false to PROJECT-CONTEXT.md.

    Args:
        triggered: True to activate the circuit breaker, False to reset it.
    """
    if not _CONTEXT_FILE.exists():
        logger.warning("PROJECT-CONTEXT.md not found; cannot set circuit breaker.")
        return

    text = _CONTEXT_FILE.read_text(encoding="utf-8")
    value = "true" if triggered else "false"

    updated = re.sub(
        r"(circuit_breaker_triggered\s*[:=]\s*)\w+",
        lambda m: f"{m.group(1)}{value}",
        text,
        flags=re.IGNORECASE,
    )

    if updated == text:
        updated = text.rstrip() + f"\ncircuit_breaker_triggered: {value}\n"

    _CONTEXT_FILE.write_text(updated, encoding="utf-8")
    logger.info("circuit_breaker_triggered set to %s", value)


def get_peak_equity() -> float:
    """Return peak_equity from PROJECT-CONTEXT.md, defaulting to 0.0."""
    return float(get_portfolio_state().get("peak_equity", 0.0))


def update_weekly_trade_count(count: int) -> None:
    """Write weekly_trade_count: N to PROJECT-CONTEXT.md."""
    if not _CONTEXT_FILE.exists():
        return

    text = _CONTEXT_FILE.read_text(encoding="utf-8")

    updated = re.sub(
        r"(weekly_trade_count\s*[:=]\s*)\d+",
        lambda m: f"{m.group(1)}{count}",
        text,
        flags=re.IGNORECASE,
    )

    if updated == text:
        updated = text.rstrip() + f"\nweekly_trade_count: {count}\n"

    _CONTEXT_FILE.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Weekly trade count
# ---------------------------------------------------------------------------

def get_weekly_trade_count() -> int:
    """
    Count the number of trades logged in TRADE-LOG.md since Monday of this week.

    Reads the table rows from the trade log and counts entries where the date
    column falls within the current Monday-to-Sunday window.

    Returns:
        Integer count of trades this week.
    """
    if not _TRADE_LOG_FILE.exists():
        return 0

    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())

    try:
        text = _TRADE_LOG_FILE.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Could not read TRADE-LOG.md: %s", exc)
        return 0

    count = 0
    # Match table rows: | YYYY-MM-DD | SYMBOL | ...
    row_pattern = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|", re.MULTILINE)

    for match in row_pattern.finditer(text):
        try:
            row_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            if row_date >= monday:
                count += 1
        except ValueError:
            continue

    logger.debug("Weekly trade count: %d (since %s)", count, monday)
    return count
