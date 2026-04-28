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

from shark.memory.atomic import atomic_write_text, file_lock

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # shark-trading-agent/
_MEMORY_DIR = _PROJECT_ROOT / "memory"
_CONTEXT_FILE = _MEMORY_DIR / "PROJECT-CONTEXT.md"
_TRADE_LOG_FILE = _MEMORY_DIR / "TRADE-LOG.md"
_STATE_LOCK = _MEMORY_DIR / ".project-context.lock"

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

    with file_lock(_STATE_LOCK):
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
            atomic_write_text(_CONTEXT_FILE, content)
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

        atomic_write_text(_CONTEXT_FILE, updated)
        logger.info("peak_equity updated to %.2f in PROJECT-CONTEXT.md", new_equity)


# ---------------------------------------------------------------------------
# Git memory commit
# ---------------------------------------------------------------------------

_PUSH_FAILED_FLAG = _MEMORY_DIR / "PUSH-FAILED.flag"


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand in the project root and return its result."""
    return subprocess.run(
        ["git", *args],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _record_push_failure(reason: str) -> None:
    """Write a sticky flag so the next routine sees we have unsynced state.

    Operator must investigate, manually merge / push, and remove the flag.
    The flag itself is committed to git so any host pulling main sees it.
    """
    try:
        atomic_write_text(
            _PUSH_FAILED_FLAG,
            "Memory push failed and was NOT silently overwritten with remote.\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Reason: {reason}\n\n"
            "This means today's local writes (trade log, sidecars, state) are\n"
            "still on this host but not on origin/main. Operator action required:\n"
            "  1. Inspect the conflict in memory/ vs origin/main.\n"
            "  2. Manually merge / commit / push.\n"
            "  3. rm memory/PUSH-FAILED.flag and commit the removal.\n",
        )
    except Exception as exc:  # never let alerting fail the whole routine
        logger.error("Could not write PUSH-FAILED.flag: %s", exc)


def commit_memory(message: str) -> bool:
    """
    Stage all files in memory/ and create a git commit, then push.

    Conflict policy (CHANGED — was destructive, now safe):
        On a push collision we attempt ONE rebase pull and ONE retry push.
        If either fails we DO NOT auto-resolve with --theirs / --ours: that
        previously could overwrite today's trade log, attribution sidecar,
        or circuit-breaker state with stale remote data. Instead we drop a
        memory/PUSH-FAILED.flag (which is committed and pushed on the next
        successful run) and return False so the operator is alerted.

    Args:
        message: Commit message.

    Returns:
        True only when the commit was successfully pushed to origin/main.
        False on any failure — the caller must treat this as a hard error.
    """
    try:
        add_result = _git("add", "memory/", "docs/dashboard/", timeout=30)
        if add_result.returncode != 0:
            logger.error("git add failed: %s", add_result.stderr)
            return False

        status = _git("status", "--porcelain", "memory/", "docs/dashboard/", timeout=30)
        if not status.stdout.strip():
            logger.info("No changes in memory/ to commit.")
            return True

        commit = _git("commit", "-m", message, timeout=30)
        if commit.returncode != 0:
            logger.error("git commit failed: %s", commit.stderr)
            return False
        logger.info("Memory committed: %s", message)

        push = _git("push", "origin", "HEAD:main")
        if push.returncode == 0:
            logger.info("Memory pushed to origin/main")
            # If we previously failed and the operator has now resolved it,
            # an explicit rm of the flag would be needed; never auto-clear
            # because clearing without operator review is exactly the bug we
            # are trying to prevent.
            return True

        # First push failed — try ONE rebase pull then ONE retry push.
        logger.warning("Initial push failed: %s", push.stderr.strip()[:200])
        rebase = _git("pull", "--rebase", "origin", "main")
        if rebase.returncode != 0:
            # Rebase conflict. Abort to leave the working tree clean, then
            # alert. We do NOT auto-resolve with --theirs because the conflict
            # may be on TRADE-LOG.md / open-trades.json where remote-wins
            # would silently lose today's writes.
            logger.error(
                "git rebase failed and we refuse to auto-resolve. stderr=%s",
                rebase.stderr.strip()[:200],
            )
            _git("rebase", "--abort", timeout=30)
            _record_push_failure(
                "rebase conflict on memory/* — operator must manually merge "
                "to avoid overwriting trade log / attribution / state."
            )
            return False

        retry = _git("push", "origin", "HEAD:main")
        if retry.returncode != 0:
            logger.error(
                "git push retry failed after clean rebase: %s",
                retry.stderr.strip()[:200],
            )
            _record_push_failure(
                f"push retry failed after rebase: {retry.stderr.strip()[:200]}"
            )
            return False

        logger.info("Memory pushed to origin/main (after rebase retry)")
        return True

    except subprocess.TimeoutExpired:
        logger.error("git operation timed out.")
        _record_push_failure("git operation timed out (network or hook hang)")
        return False
    except Exception as exc:
        logger.error("Unexpected error during git commit/push: %s", exc)
        _record_push_failure(f"unexpected exception: {exc}")
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
    with file_lock(_STATE_LOCK):
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

        atomic_write_text(_CONTEXT_FILE, updated)
        logger.info("circuit_breaker_triggered set to %s", value)


def get_peak_equity() -> float:
    """Return peak_equity from PROJECT-CONTEXT.md, defaulting to 0.0."""
    return float(get_portfolio_state().get("peak_equity", 0.0))


# ---------------------------------------------------------------------------
# Weekly trade count — derived from TRADE-LOG.md (single source of truth)
# ---------------------------------------------------------------------------
#
# Historical note: there used to be an `update_weekly_trade_count(count)` setter
# that wrote a `weekly_trade_count:` line into PROJECT-CONTEXT.md. The getter
# below has always derived the count by scanning TRADE-LOG.md, so the setter's
# writes were never read. The setter has been removed to eliminate the dead
# write and the misleading API surface. Any historical `weekly_trade_count:`
# line in PROJECT-CONTEXT.md is harmless and will be overwritten the next time
# the file is rewritten.
#

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
