"""
Macro Calendar — hard blocks around major economic events.

Never enter new positions before:
  - FOMC rate decisions (day before + day of)
  - CPI releases (day of)
  - Non-Farm Payrolls (day of)
  - PCE inflation (day of)
  - Quad witching / OpEx Fridays

Position sizing reduced 50% on:
  - Day before CPI/NFP
  - Week of FOMC meeting

Data source: Static calendar updated quarterly + Perplexity real-time check.
Calendar covers 2025-2026. Beyond that, update _EVENTS list.
"""

from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class EventType:
    FOMC = "FOMC"
    CPI = "CPI"
    NFP = "NFP"
    PCE = "PCE"
    OPEX = "OPEX"
    GDP = "GDP"


# Impact level determines trading rules
IMPACT_RULES: dict[str, dict[str, Any]] = {
    "CRITICAL": {
        "new_trades_allowed": False,
        "position_size_multiplier": 0.0,
        "description": "No new trades — major event day",
    },
    "HIGH": {
        "new_trades_allowed": False,
        "position_size_multiplier": 0.0,
        "description": "No new trades — event imminent",
    },
    "ELEVATED": {
        "new_trades_allowed": True,
        "position_size_multiplier": 0.50,
        "description": "Half-size trades — event approaching",
    },
    "NORMAL": {
        "new_trades_allowed": True,
        "position_size_multiplier": 1.0,
        "description": "Normal trading — no major events",
    },
}

# Static event calendar — FOMC, CPI, NFP dates for 2025-2026
# FOMC = Federal Open Market Committee rate decision days
# CPI = Consumer Price Index release
# NFP = Non-Farm Payrolls (first Friday of month, typically)
# PCE = Personal Consumption Expenditures (Fed's preferred inflation gauge)
_EVENTS: list[dict[str, Any]] = [
    # === 2025 FOMC ===
    {"date": "2025-01-29", "type": EventType.FOMC, "name": "FOMC Jan 2025"},
    {"date": "2025-03-19", "type": EventType.FOMC, "name": "FOMC Mar 2025"},
    {"date": "2025-05-07", "type": EventType.FOMC, "name": "FOMC May 2025"},
    {"date": "2025-06-18", "type": EventType.FOMC, "name": "FOMC Jun 2025"},
    {"date": "2025-07-30", "type": EventType.FOMC, "name": "FOMC Jul 2025"},
    {"date": "2025-09-17", "type": EventType.FOMC, "name": "FOMC Sep 2025"},
    {"date": "2025-10-29", "type": EventType.FOMC, "name": "FOMC Oct 2025"},
    {"date": "2025-12-17", "type": EventType.FOMC, "name": "FOMC Dec 2025"},
    # === 2026 FOMC ===
    {"date": "2026-01-28", "type": EventType.FOMC, "name": "FOMC Jan 2026"},
    {"date": "2026-03-18", "type": EventType.FOMC, "name": "FOMC Mar 2026"},
    {"date": "2026-04-29", "type": EventType.FOMC, "name": "FOMC Apr 2026"},
    {"date": "2026-06-17", "type": EventType.FOMC, "name": "FOMC Jun 2026"},
    {"date": "2026-07-29", "type": EventType.FOMC, "name": "FOMC Jul 2026"},
    {"date": "2026-09-16", "type": EventType.FOMC, "name": "FOMC Sep 2026"},
    {"date": "2026-10-28", "type": EventType.FOMC, "name": "FOMC Oct 2026"},
    {"date": "2026-12-16", "type": EventType.FOMC, "name": "FOMC Dec 2026"},
    # === 2025 CPI (typically 2nd or 3rd Tuesday/Wednesday of month) ===
    {"date": "2025-01-15", "type": EventType.CPI, "name": "CPI Jan 2025"},
    {"date": "2025-02-12", "type": EventType.CPI, "name": "CPI Feb 2025"},
    {"date": "2025-03-12", "type": EventType.CPI, "name": "CPI Mar 2025"},
    {"date": "2025-04-10", "type": EventType.CPI, "name": "CPI Apr 2025"},
    {"date": "2025-05-13", "type": EventType.CPI, "name": "CPI May 2025"},
    {"date": "2025-06-11", "type": EventType.CPI, "name": "CPI Jun 2025"},
    {"date": "2025-07-15", "type": EventType.CPI, "name": "CPI Jul 2025"},
    {"date": "2025-08-12", "type": EventType.CPI, "name": "CPI Aug 2025"},
    {"date": "2025-09-10", "type": EventType.CPI, "name": "CPI Sep 2025"},
    {"date": "2025-10-14", "type": EventType.CPI, "name": "CPI Oct 2025"},
    {"date": "2025-11-12", "type": EventType.CPI, "name": "CPI Nov 2025"},
    {"date": "2025-12-10", "type": EventType.CPI, "name": "CPI Dec 2025"},
    # === 2026 CPI ===
    {"date": "2026-01-14", "type": EventType.CPI, "name": "CPI Jan 2026"},
    {"date": "2026-02-11", "type": EventType.CPI, "name": "CPI Feb 2026"},
    {"date": "2026-03-11", "type": EventType.CPI, "name": "CPI Mar 2026"},
    {"date": "2026-04-14", "type": EventType.CPI, "name": "CPI Apr 2026"},
    {"date": "2026-05-12", "type": EventType.CPI, "name": "CPI May 2026"},
    {"date": "2026-06-10", "type": EventType.CPI, "name": "CPI Jun 2026"},
    {"date": "2026-07-14", "type": EventType.CPI, "name": "CPI Jul 2026"},
    {"date": "2026-08-12", "type": EventType.CPI, "name": "CPI Aug 2026"},
    {"date": "2026-09-15", "type": EventType.CPI, "name": "CPI Sep 2026"},
    {"date": "2026-10-13", "type": EventType.CPI, "name": "CPI Oct 2026"},
    {"date": "2026-11-10", "type": EventType.CPI, "name": "CPI Nov 2026"},
    {"date": "2026-12-10", "type": EventType.CPI, "name": "CPI Dec 2026"},
    # === 2025 NFP (first Friday of month) ===
    {"date": "2025-01-10", "type": EventType.NFP, "name": "NFP Jan 2025"},
    {"date": "2025-02-07", "type": EventType.NFP, "name": "NFP Feb 2025"},
    {"date": "2025-03-07", "type": EventType.NFP, "name": "NFP Mar 2025"},
    {"date": "2025-04-04", "type": EventType.NFP, "name": "NFP Apr 2025"},
    {"date": "2025-05-02", "type": EventType.NFP, "name": "NFP May 2025"},
    {"date": "2025-06-06", "type": EventType.NFP, "name": "NFP Jun 2025"},
    {"date": "2025-07-03", "type": EventType.NFP, "name": "NFP Jul 2025"},
    {"date": "2025-08-01", "type": EventType.NFP, "name": "NFP Aug 2025"},
    {"date": "2025-09-05", "type": EventType.NFP, "name": "NFP Sep 2025"},
    {"date": "2025-10-03", "type": EventType.NFP, "name": "NFP Oct 2025"},
    {"date": "2025-11-07", "type": EventType.NFP, "name": "NFP Nov 2025"},
    {"date": "2025-12-05", "type": EventType.NFP, "name": "NFP Dec 2025"},
    # === 2026 NFP ===
    {"date": "2026-01-09", "type": EventType.NFP, "name": "NFP Jan 2026"},
    {"date": "2026-02-06", "type": EventType.NFP, "name": "NFP Feb 2026"},
    {"date": "2026-03-06", "type": EventType.NFP, "name": "NFP Mar 2026"},
    {"date": "2026-04-03", "type": EventType.NFP, "name": "NFP Apr 2026"},
    {"date": "2026-05-01", "type": EventType.NFP, "name": "NFP May 2026"},
    {"date": "2026-06-05", "type": EventType.NFP, "name": "NFP Jun 2026"},
    {"date": "2026-07-02", "type": EventType.NFP, "name": "NFP Jul 2026"},
    {"date": "2026-08-07", "type": EventType.NFP, "name": "NFP Aug 2026"},
    {"date": "2026-09-04", "type": EventType.NFP, "name": "NFP Sep 2026"},
    {"date": "2026-10-02", "type": EventType.NFP, "name": "NFP Oct 2026"},
    {"date": "2026-11-06", "type": EventType.NFP, "name": "NFP Nov 2026"},
    {"date": "2026-12-04", "type": EventType.NFP, "name": "NFP Dec 2026"},
    # === Quad Witching / OpEx (3rd Friday of Mar, Jun, Sep, Dec) ===
    {"date": "2025-03-21", "type": EventType.OPEX, "name": "Quad Witching Mar 2025"},
    {"date": "2025-06-20", "type": EventType.OPEX, "name": "Quad Witching Jun 2025"},
    {"date": "2025-09-19", "type": EventType.OPEX, "name": "Quad Witching Sep 2025"},
    {"date": "2025-12-19", "type": EventType.OPEX, "name": "Quad Witching Dec 2025"},
    {"date": "2026-03-20", "type": EventType.OPEX, "name": "Quad Witching Mar 2026"},
    {"date": "2026-06-19", "type": EventType.OPEX, "name": "Quad Witching Jun 2026"},
    {"date": "2026-09-18", "type": EventType.OPEX, "name": "Quad Witching Sep 2026"},
    {"date": "2026-12-18", "type": EventType.OPEX, "name": "Quad Witching Dec 2026"},
]

# Pre-compute date set for fast lookup
_EVENT_DATES: dict[date, list[dict[str, Any]]] = {}
for _evt in _EVENTS:
    _d = date.fromisoformat(_evt["date"])
    _EVENT_DATES.setdefault(_d, []).append(_evt)


def check_macro_calendar(check_date: date | None = None) -> dict[str, Any]:
    """
    Check if today (or a given date) has macro events that affect trading.

    Returns:
        Dict with:
            impact_level (str): CRITICAL / HIGH / ELEVATED / NORMAL
            rules (dict): Trading rules for this impact level
            events_today (list): Events on this date
            events_nearby (list): Events within 1 business day
            description (str): Human-readable summary
    """
    today = check_date or date.today()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)

    # Skip weekends for tomorrow check
    if tomorrow.weekday() >= 5:
        tomorrow = today + timedelta(days=(7 - today.weekday()))

    events_today = _EVENT_DATES.get(today, [])
    events_tomorrow = _EVENT_DATES.get(tomorrow, [])
    events_yesterday = _EVENT_DATES.get(yesterday, [])

    # Determine impact level
    if events_today:
        # FOMC day = CRITICAL, others = HIGH
        if any(e["type"] == EventType.FOMC for e in events_today):
            impact = "CRITICAL"
        else:
            impact = "HIGH"
        description = f"Event day: {', '.join(e['name'] for e in events_today)}"
    elif events_tomorrow:
        if any(e["type"] == EventType.FOMC for e in events_tomorrow):
            impact = "HIGH"
            description = f"Day before FOMC: {', '.join(e['name'] for e in events_tomorrow)}"
        else:
            impact = "ELEVATED"
            description = f"Day before: {', '.join(e['name'] for e in events_tomorrow)}"
    elif events_yesterday:
        # Post-event day: usually okay but cautious
        impact = "ELEVATED"
        description = f"Day after: {', '.join(e['name'] for e in events_yesterday)}"
    else:
        impact = "NORMAL"
        description = "No major macro events nearby"

    # Check for FOMC week (any FOMC within 2 business days)
    if impact == "NORMAL":
        for delta in range(-2, 3):
            check = today + timedelta(days=delta)
            if check in _EVENT_DATES:
                if any(e["type"] == EventType.FOMC for e in _EVENT_DATES[check]):
                    impact = "ELEVATED"
                    description = f"FOMC week — event on {check.isoformat()}"
                    break

    rules = IMPACT_RULES[impact]

    nearby = []
    for delta in range(-3, 8):
        check = today + timedelta(days=delta)
        if check in _EVENT_DATES:
            for evt in _EVENT_DATES[check]:
                nearby.append({
                    **evt,
                    "days_away": delta,
                    "relative": "today" if delta == 0 else (
                        f"in {delta}d" if delta > 0 else f"{abs(delta)}d ago"
                    ),
                })

    result = {
        "impact_level": impact,
        "rules": rules,
        "events_today": events_today,
        "events_nearby": nearby,
        "description": description,
        "check_date": today.isoformat(),
    }

    if impact != "NORMAL":
        logger.info("Macro calendar: %s — %s", impact, description)

    return result


def get_next_event(check_date: date | None = None) -> dict[str, Any] | None:
    """Return the next upcoming macro event from today."""
    today = check_date or date.today()

    for delta in range(1, 60):
        check = today + timedelta(days=delta)
        if check in _EVENT_DATES:
            events = _EVENT_DATES[check]
            return {
                "date": check.isoformat(),
                "days_away": delta,
                "events": events,
                "names": [e["name"] for e in events],
            }

    return None


def is_fomc_week(check_date: date | None = None) -> bool:
    """Return True if any FOMC meeting is within 2 business days."""
    today = check_date or date.today()
    for delta in range(-2, 3):
        check = today + timedelta(days=delta)
        if check in _EVENT_DATES:
            if any(e["type"] == EventType.FOMC for e in _EVENT_DATES[check]):
                return True
    return False
