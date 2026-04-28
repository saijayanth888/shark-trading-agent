"""
Kill switch — operator-controlled hard stop.

Mechanism:
    Place a file at memory/KILL.flag (any contents) to halt all trading
    phases. Each trading phase calls `enforce_kill_switch()` immediately
    after starting and refuses to proceed if the flag exists.

    The non-trading phases (kb-refresh, kb-update) ignore the flag so that
    research and data hygiene can continue while trading is paused.

Why a file flag and not env var or DB:
    - Survives process restarts and cloud-container churn.
    - Trivial for an operator to toggle via `touch memory/KILL.flag` /
      `rm memory/KILL.flag` on any host that has the repo checked out.
    - Committed to git on creation so every routine sees it (eventual
      consistency window aside).
    - Cannot be silently overwritten by a failing state-write — it's a
      separate file with no automated removal path inside the agent.

Operator usage:
    # halt
    touch memory/KILL.flag && git add memory/KILL.flag && \
        git commit -m "ops: halt trading" && git push

    # resume
    git rm memory/KILL.flag && git commit -m "ops: resume trading" && git push
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KILL_FLAG = _PROJECT_ROOT / "memory" / "KILL.flag"


class KillSwitchActive(RuntimeError):
    """Raised by enforce_kill_switch() when the operator flag is present."""


def is_killed() -> bool:
    """Return True if the operator kill switch is currently engaged."""
    return _KILL_FLAG.exists()


def kill_reason() -> str:
    """Return the contents of the kill flag (if any) as a human-readable reason."""
    if not _KILL_FLAG.exists():
        return ""
    try:
        return _KILL_FLAG.read_text(encoding="utf-8").strip() or "(no reason given)"
    except OSError:
        return "(could not read KILL.flag)"


def enforce_kill_switch(phase: str) -> None:
    """
    Block a trading phase if the kill switch is engaged.

    Trading phases should call this once at entry, before any data fetch
    or order placement. Raises KillSwitchActive on engagement so the caller
    can decide between hard-fail and graceful-skip.

    Args:
        phase: Phase name, used only in the log message and exception text.

    Raises:
        KillSwitchActive: when memory/KILL.flag exists.
    """
    if is_killed():
        reason = kill_reason()
        logger.error(
            "KILL SWITCH ENGAGED — phase=%s refusing to run. Reason: %s",
            phase, reason,
        )
        raise KillSwitchActive(
            f"Kill switch engaged for phase '{phase}'. Remove memory/KILL.flag to resume. "
            f"Reason: {reason}"
        )


__all__ = [
    "KillSwitchActive",
    "is_killed",
    "kill_reason",
    "enforce_kill_switch",
]
