"""
Daily Handoff — compact phase-to-phase state file.

Each routine appends a key:value block to memory/DAILY-HANDOFF.md.
The next routine reads only its relevant section instead of scanning full log files.
File is reset daily by pre-market. All reads fall back gracefully if missing.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_HANDOFF_FILE = _PROJECT_ROOT / "memory" / "DAILY-HANDOFF.md"


def reset_daily_handoff() -> None:
    """Create a fresh handoff file for today. Called once at pre-market start."""
    _HANDOFF_FILE.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    _HANDOFF_FILE.write_text(f"# Daily Handoff — {today}\n", encoding="utf-8")
    logger.info("Daily handoff reset for %s", today)


def write_handoff_section(phase: str, data: dict) -> None:
    """
    Append a phase summary block to DAILY-HANDOFF.md.

    Args:
        phase: Phase name, e.g. "pre-market"
        data: Key:value pairs, e.g. {"confirmed": "NVDA, MSFT"}
    """
    _HANDOFF_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H:%M EDT")
    lines = [f"{k}: {v}" for k, v in data.items()]
    block = f"\n## {phase} | {timestamp}\n" + "\n".join(lines) + "\n"

    if not _HANDOFF_FILE.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        _HANDOFF_FILE.write_text(
            f"# Daily Handoff — {today}\n{block}", encoding="utf-8"
        )
    else:
        with _HANDOFF_FILE.open("a", encoding="utf-8") as f:
            f.write(block)

    logger.info("Handoff section written: phase=%s keys=%s", phase, list(data.keys()))


def read_handoff_section(phase: str) -> dict:
    """
    Read key:value pairs from a phase block in today's DAILY-HANDOFF.md.

    Returns empty dict if file or section is missing — callers must handle fallback.
    """
    if not _HANDOFF_FILE.exists():
        return {}

    try:
        text = _HANDOFF_FILE.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not read DAILY-HANDOFF.md: %s", exc)
        return {}

    pattern = re.compile(
        rf"^## {re.escape(phase)}\s*\|.*?$(.+?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return {}

    result = {}
    for line in match.group(1).strip().splitlines():
        if ": " in line:
            key, _, val = line.partition(": ")
            result[key.strip()] = val.strip()

    return result


def get_confirmed_symbols() -> list[str]:
    """Return pre-market confirmed symbols from today's handoff."""
    raw = read_handoff_section("pre-market").get("confirmed", "")
    if not raw or raw.lower() == "none":
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_validated_symbols() -> list[str]:
    """Return pre-execute validated symbols from today's handoff."""
    raw = read_handoff_section("pre-execute").get("validated", "")
    if not raw or raw.lower() == "none":
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]
