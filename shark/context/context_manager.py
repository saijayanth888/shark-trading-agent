"""
Context Manager — builds phase-specific, token-efficient context briefings.

Cloud routines (Claude) degrade when fed the entire memory directory.
This module generates a compact CONTEXT-BRIEFING.md before each phase,
containing ONLY what that phase needs — compressed, sectioned, and
within a token budget.

Usage:
    from shark.context.context_manager import generate_context_briefing
    generate_context_briefing("market-open")
    # → writes memory/CONTEXT-BRIEFING.md with focused context
"""

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MEMORY_DIR = _PROJECT_ROOT / "memory"
_BRIEFING_FILE = _MEMORY_DIR / "CONTEXT-BRIEFING.md"

# Rough token estimate: 1 token ≈ 4 characters
_MAX_TOKENS = 4000  # target budget per briefing (~16k chars)

# ---------------------------------------------------------------------------
# Phase → context requirements map
# ---------------------------------------------------------------------------
# Each phase declares what it needs from memory files.
# Format: list of (file_stem, extraction_method, params)

_PHASE_MANIFEST: dict[str, list[tuple[str, str, dict]]] = {
    "pre-market": [
        ("TRADING-STRATEGY", "sections", {"headings": ["Watchlist", "Entry Criteria", "Market Regime", "Macro Calendar"]}),
        ("PROJECT-CONTEXT", "full", {}),
        ("TRADE-LOG", "tail", {"n_lines": 10}),
        ("LESSONS-LEARNED", "tail", {"n_lines": 10}),
    ],
    "pre-execute": [
        ("DAILY-HANDOFF", "sections", {"headings": ["pre-market"]}),
        ("RESEARCH-LOG", "today", {}),
        ("PROJECT-CONTEXT", "keys", {"keys": ["current_mode", "circuit_breaker_triggered"]}),
    ],
    "market-open": [
        ("DAILY-HANDOFF", "sections", {"headings": ["pre-market", "pre-execute"]}),
        ("PROJECT-CONTEXT", "keys", {"keys": ["peak_equity", "current_mode", "circuit_breaker_triggered"]}),
        ("TRADING-STRATEGY", "sections", {"headings": ["Entry Criteria", "Position Sizing", "Exit Management", "Market Regime", "Relative Strength"]}),
        ("TRADE-LOG", "tail", {"n_lines": 8}),
        ("LESSONS-LEARNED", "tail", {"n_lines": 8}),
    ],
    "midday": [
        ("DAILY-HANDOFF", "sections", {"headings": ["market-open"]}),
        ("PROJECT-CONTEXT", "keys", {"keys": ["peak_equity", "circuit_breaker_triggered"]}),
        ("TRADING-STRATEGY", "sections", {"headings": ["Exit Management", "Market Regime", "Partial Profit"]}),
        ("TRADE-LOG", "today", {}),
    ],
    "daily-summary": [
        ("PROJECT-CONTEXT", "full", {}),
        ("DAILY-HANDOFF", "full", {}),
        ("TRADE-LOG", "today", {}),
        ("TRADING-STRATEGY", "sections", {"headings": ["Circuit Breaker", "Strategy Review"]}),
    ],
    "weekly-review": [
        ("PROJECT-CONTEXT", "full", {}),
        ("TRADE-LOG", "this_week", {}),
        ("RESEARCH-LOG", "this_week", {}),
        ("WEEKLY-REVIEW", "tail", {"n_lines": 30}),
        ("TRADING-STRATEGY", "sections", {"headings": ["Strategy Review", "Adaptive Learning"]}),
        ("LESSONS-LEARNED", "tail", {"n_lines": 15}),
    ],
    "backtest": [
        ("PROJECT-CONTEXT", "keys", {"keys": ["peak_equity", "current_mode"]}),
        ("TRADING-STRATEGY", "sections", {"headings": ["Position Sizing", "Entry Criteria", "Exit Management", "Market Regime"]}),
        ("BACKTEST-REPORT", "tail", {"n_lines": 40}),
    ],
}

# Phase objectives — tells the Claude routine exactly what to accomplish
_PHASE_OBJECTIVES: dict[str, str] = {
    "pre-market": (
        "Scan watchlist, detect regime + macro context, rank by RS and sentiment, "
        "identify top candidates for market-open. Write handoff with confirmed symbols."
    ),
    "pre-execute": (
        "Validate pre-market candidates using first 30min of trading data. "
        "Check volume, price action, and news. Write validated symbols to handoff."
    ),
    "market-open": (
        "Execute trades for validated candidates. Apply regime gates, macro blocks, "
        "RS filter, ATR sizing, guardrails. Place bracket orders for approved trades."
    ),
    "midday": (
        "Manage open positions: run exit manager, check hard stops, tighten trails, "
        "detect volatility expansion, check thesis breaks. Review closed trades."
    ),
    "daily-summary": (
        "Calculate daily P&L, update peak equity, check circuit breaker, "
        "write daily summary, send digest email."
    ),
    "weekly-review": (
        "Compute weekly returns, win rate, profit factor, alpha vs SPY. "
        "Grade performance, identify patterns, plan next week."
    ),
    "backtest": (
        "Run historical backtest against last 12 months of data using current "
        "strategy parameters. Generate BACKTEST-REPORT.md with metrics, regime "
        "analysis, and parameter recommendations. No real trades placed."
    ),
}


# ---------------------------------------------------------------------------
# Extraction methods
# ---------------------------------------------------------------------------

def _read_file_safe(path: Path) -> str:
    """Read file or return empty string."""
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not read %s: %s", path.name, exc)
    return ""


def _extract_full(text: str, _params: dict) -> str:
    """Return full file content."""
    return text


def _extract_tail(text: str, params: dict) -> str:
    """Return last N lines of file."""
    n = params.get("n_lines", 15)
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[-n:])


def _extract_sections(text: str, params: dict) -> str:
    """Extract only named markdown sections (## heading)."""
    headings = params.get("headings", [])
    if not headings:
        return text

    lines = text.splitlines()
    result_parts: list[str] = []

    for heading in headings:
        heading_lower = heading.lower()
        capturing = False
        captured: list[str] = []
        capture_level = 0

        for line in lines:
            # Check if this line is a markdown heading
            heading_match = re.match(r"^(#{1,3})\s+(.*)", line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2)

                if heading_lower in title.lower():
                    # Start capturing this section
                    capturing = True
                    capture_level = level
                    captured = [line]
                    continue
                elif capturing and level <= capture_level:
                    # Hit a same-or-higher-level heading — stop capturing
                    capturing = False
                    continue

            if capturing:
                captured.append(line)

        if captured:
            result_parts.append("\n".join(captured).strip())

    return "\n\n".join(result_parts) if result_parts else f"(no sections matched: {headings})"


def _extract_today(text: str, _params: dict) -> str:
    """Extract only today's entries from a markdown file with date headers."""
    today_str = date.today().isoformat()
    pattern = re.compile(
        rf"^(#{1,3}\s+{re.escape(today_str)}.*)$(.*?)(?=^#{1,3}\s+\d{{4}}-\d{{2}}-\d{{2}}|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        return (match.group(1) + match.group(2)).strip()

    # Fallback: scan for date in table rows
    lines = text.splitlines()
    today_lines = [l for l in lines if today_str in l]
    if today_lines:
        return "\n".join(today_lines)

    return f"(no entries for {today_str})"


def _extract_this_week(text: str, _params: dict) -> str:
    """Extract entries from the current Monday-Sunday window."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    dates_this_week = [(monday + timedelta(days=i)).isoformat() for i in range(7)]

    lines = text.splitlines()
    result: list[str] = []
    in_week_section = False

    for line in lines:
        # Check if this line starts a date section within this week
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", line)
        if date_match:
            if date_match.group() in dates_this_week:
                in_week_section = True
                result.append(line)
                continue
            elif in_week_section and line.startswith("#"):
                in_week_section = False
                continue

        if in_week_section:
            result.append(line)
        elif any(d in line for d in dates_this_week):
            result.append(line)

    return "\n".join(result) if result else f"(no entries for week of {monday.isoformat()})"


def _extract_keys(text: str, params: dict) -> str:
    """Extract only specific key: value lines from a context file."""
    keys = params.get("keys", [])
    if not keys:
        return text

    result: list[str] = []
    for key in keys:
        pattern = re.compile(rf"^.*{re.escape(key)}\s*[:=]\s*(.+)$", re.MULTILINE | re.IGNORECASE)
        match = pattern.search(text)
        if match:
            result.append(f"{key}: {match.group(1).strip()}")
        else:
            result.append(f"{key}: (not found)")

    return "\n".join(result)


_EXTRACTORS = {
    "full": _extract_full,
    "tail": _extract_tail,
    "sections": _extract_sections,
    "today": _extract_today,
    "this_week": _extract_this_week,
    "keys": _extract_keys,
}


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token count — 1 token ≈ 4 characters."""
    return len(text) // 4


def _trim_to_budget(sections: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    """Trim sections from the end if total exceeds token budget."""
    total = sum(estimate_tokens(content) for _, content in sections)
    if total <= budget:
        return sections

    logger.warning("Context %d tokens exceeds budget %d — trimming", total, budget)

    # Trim longest non-critical sections first
    trimmed = list(sections)
    while estimate_tokens("\n".join(c for _, c in trimmed)) > budget and len(trimmed) > 1:
        # Find longest section that isn't the first (objective/handoff)
        longest_idx = max(range(1, len(trimmed)), key=lambda i: len(trimmed[i][1]))
        name, content = trimmed[longest_idx]
        lines = content.splitlines()
        # Cut to half
        half = max(5, len(lines) // 2)
        trimmed[longest_idx] = (name, "\n".join(lines[:half]) + f"\n... (trimmed {len(lines) - half} lines)")

    return trimmed


# ---------------------------------------------------------------------------
# Briefing generator
# ---------------------------------------------------------------------------

def build_phase_context(phase: str) -> str:
    """
    Build a compact context string for a specific trading phase.

    Args:
        phase: One of the phase names from run.py (e.g. "market-open")

    Returns:
        Markdown string with phase-specific compressed context.
    """
    manifest = _PHASE_MANIFEST.get(phase)
    if not manifest:
        return f"# Context Briefing — {phase}\n\n(unknown phase — no manifest defined)\n"

    objective = _PHASE_OBJECTIVES.get(phase, "Execute phase logic.")
    today_str = date.today().isoformat()
    now_str = datetime.now().strftime("%H:%M EDT")

    sections: list[tuple[str, str]] = []

    # Header
    header = (
        f"# Context Briefing — {phase}\n"
        f"Generated: {today_str} {now_str}\n\n"
        f"## Phase Objective\n"
        f"{objective}\n"
    )
    sections.append(("header", header))

    # Extract each required context source
    for file_stem, method, params in manifest:
        file_path = _MEMORY_DIR / f"{file_stem}.md"
        raw = _read_file_safe(file_path)

        if not raw:
            sections.append((file_stem, f"### {file_stem}\n(file empty or missing)\n"))
            continue

        extractor = _EXTRACTORS.get(method, _extract_full)
        extracted = extractor(raw, params)

        if extracted:
            sections.append((file_stem, f"### {file_stem}\n{extracted}\n"))

    # Trim to budget
    sections = _trim_to_budget(sections, _MAX_TOKENS)

    # Assemble
    briefing = "\n".join(content for _, content in sections)

    # Footer with token stats
    token_count = estimate_tokens(briefing)
    briefing += (
        f"\n---\n"
        f"Context tokens: ~{token_count} | Budget: {_MAX_TOKENS} | "
        f"Sources: {len(manifest)} files | Phase: {phase}\n"
    )

    return briefing


def generate_context_briefing(phase: str) -> Path:
    """
    Generate and write CONTEXT-BRIEFING.md for the given phase.

    This should be called at the START of each cloud routine, before
    the Claude agent reads any memory files. The briefing replaces
    manual file reads with a single, focused, compressed document.

    Args:
        phase: Trading phase name (e.g. "market-open")

    Returns:
        Path to the written briefing file.
    """
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    briefing = build_phase_context(phase)
    _BRIEFING_FILE.write_text(briefing, encoding="utf-8")

    token_count = estimate_tokens(briefing)
    logger.info(
        "Context briefing generated: phase=%s tokens=~%d file=%s",
        phase, token_count, _BRIEFING_FILE.name,
    )

    return _BRIEFING_FILE


def get_context_briefing() -> str:
    """Read the current context briefing if it exists."""
    return _read_file_safe(_BRIEFING_FILE)


# ---------------------------------------------------------------------------
# Context health check — useful for monitoring degradation
# ---------------------------------------------------------------------------

def check_context_health() -> dict[str, Any]:
    """
    Report on memory file sizes and estimated token costs.

    Returns dict with per-file token estimates and a total.
    Useful for monitoring context bloat over time.
    """
    report: dict[str, Any] = {"files": {}, "total_tokens": 0}

    for md_file in sorted(_MEMORY_DIR.glob("*.md")):
        text = _read_file_safe(md_file)
        tokens = estimate_tokens(text)
        lines = len(text.splitlines())
        report["files"][md_file.name] = {
            "tokens": tokens,
            "lines": lines,
            "bytes": len(text),
        }
        report["total_tokens"] += tokens

    report["budget"] = _MAX_TOKENS
    report["over_budget"] = report["total_tokens"] > _MAX_TOKENS * 3  # 3x = danger zone

    if report["over_budget"]:
        logger.warning(
            "Memory files total ~%d tokens — exceeds safe threshold (%d). "
            "Consider archiving old entries.",
            report["total_tokens"], _MAX_TOKENS * 3,
        )

    return report
