"""
KB Scoring — historical edge bonus/penalty for ticker scoring.

Wraps the read-only KB pattern API in a single high-level call:

    from shark.data.kb_scoring import compute_historical_edge

    edge = compute_historical_edge(
        symbol="NVDA",
        regime="BULL_QUIET",
        today=date(2026, 4, 28),
    )
    # edge.bonus is an int score adjustment (-15..+10)
    # edge.reject is True if a hard anti-pattern was triggered
    # edge.reasons is a list of human-readable strings for logging/email

Designed to be called from pre_market._score() and other scoring code paths.
Cold-start safe: returns zero-bonus, no-reject when KB has no patterns yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from shark.data.knowledge_base import (
    load_anti_patterns,
    load_compiled_lessons,
    load_ticker_base_rate,
)

logger = logging.getLogger(__name__)


# Tuning constants — bump these as we get more data.
_BASE_RATE_MIN_TRADES = 3              # need at least 3 trades to trust a base rate
_BASE_RATE_HIGH_WIN_THRESHOLD = 0.65    # >=65% wins → +bonus
_BASE_RATE_LOW_WIN_THRESHOLD = 0.30     # <=30% wins → -penalty
_BONUS_HIGH_WIN_RATE = 4
_PENALTY_LOW_WIN_RATE = -6              # bigger than bonus — we want to AVOID losers
_BONUS_POSITIVE_EXPECTANCY = 1
_BONUS_DOW_FAVORABLE = 1
_PENALTY_DOW_UNFAVORABLE = -1
_BONUS_FOMC_FAVORABLE = 2
_PENALTY_FOMC_UNFAVORABLE = -3


@dataclass
class HistoricalEdge:
    """Result of compute_historical_edge()."""
    bonus: int = 0
    reject: bool = False
    reasons: list[str] = field(default_factory=list)
    base_rate: dict[str, Any] | None = None
    lessons_applied: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [f"edge={self.bonus:+d}"]
        if self.reject:
            parts.append("REJECT")
        if self.reasons:
            parts.append(f"reasons={'; '.join(self.reasons[:3])}")
        return " ".join(parts)


def compute_historical_edge(
    symbol: str,
    regime: str = "",
    today: date | None = None,
) -> HistoricalEdge:
    """Compute the KB-derived score adjustment for a ticker.

    Parameters
    ----------
    symbol : str
        Ticker (e.g. "NVDA").
    regime : str, optional
        Current SPY regime label, e.g. "BULL_QUIET" / "BEAR_VOLATILE".
        Used to look up regime-specific base rates.
    today : date, optional
        The date for which we are scoring. Defaults to today.

    Returns
    -------
    HistoricalEdge
        Bonus integer + hard-reject flag + human-readable reasons.
        On cold-start (empty KB), returns a zero-bonus, no-reject result.
    """
    edge = HistoricalEdge()
    today = today or date.today()
    sym = symbol.upper()

    # ---------------------------------------------------------------
    # 1) Hard reject if symbol matches a known anti-pattern
    # ---------------------------------------------------------------
    try:
        anti = load_anti_patterns(symbol=sym)
        for pattern in anti:
            applies = pattern.get("applies_to", {})
            # Regime-specific anti-patterns
            if applies.get("regime") and applies["regime"] != regime:
                continue
            edge.reject = True
            edge.reasons.append(
                f"ANTI-PATTERN [{pattern['id']}]: "
                f"{pattern.get('description', 'historical loser')[:80]}"
            )
            return edge  # short-circuit — no need to compute further
    except Exception as exc:
        logger.debug("anti_pattern lookup failed for %s: %s", sym, exc)

    # ---------------------------------------------------------------
    # 2) Base rate bonus / penalty based on historical win rate in this regime
    # ---------------------------------------------------------------
    try:
        rate = load_ticker_base_rate(sym, regime=regime or None)
        # The KB stores per-regime stats nested by regime name; fallback to ALL if missing
        regime_stats = rate if (rate and "win_rate" in rate) else None
        if rate and not regime_stats:
            # rate is a dict of {regime: {win_rate, ...}} — pick best match
            regime_stats = rate.get(regime) or rate.get("ALL") or rate.get("UNKNOWN")
        if regime_stats:
            edge.base_rate = regime_stats
            trades = regime_stats.get("trades", 0)
            win_rate = regime_stats.get("win_rate", 0.0)
            expectancy = regime_stats.get("expectancy", 0.0)

            if trades >= _BASE_RATE_MIN_TRADES:
                if win_rate >= _BASE_RATE_HIGH_WIN_THRESHOLD:
                    edge.bonus += _BONUS_HIGH_WIN_RATE
                    edge.reasons.append(
                        f"strong base rate ({win_rate*100:.0f}% win, {trades} trades)"
                    )
                elif win_rate <= _BASE_RATE_LOW_WIN_THRESHOLD:
                    edge.bonus += _PENALTY_LOW_WIN_RATE
                    edge.reasons.append(
                        f"weak base rate ({win_rate*100:.0f}% win, {trades} trades)"
                    )
                if expectancy > 0.5:
                    edge.bonus += _BONUS_POSITIVE_EXPECTANCY
                    edge.reasons.append(f"positive expectancy ({expectancy:+.2f}%)")
    except Exception as exc:
        logger.debug("base_rate lookup failed for %s: %s", sym, exc)

    # ---------------------------------------------------------------
    # 3) Calendar effects — day-of-week tilt
    # ---------------------------------------------------------------
    try:
        from shark.data.knowledge_base import _read_json, _PATTERNS_DIR  # type: ignore
        cal = _read_json(_PATTERNS_DIR / "calendar_effects.json") or {}
        dow_map = cal.get("day_of_week", {})
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        if today.weekday() < 5:
            dow_name = dow_names[today.weekday()]
            dow_stats = dow_map.get(dow_name, {})
            avg_ret = dow_stats.get("avg_return_pct", 0)
            if avg_ret > 0.10:  # historically positive day
                edge.bonus += _BONUS_DOW_FAVORABLE
                edge.reasons.append(f"favorable {dow_name} drift ({avg_ret:+.2f}%)")
            elif avg_ret < -0.10:
                edge.bonus += _PENALTY_DOW_UNFAVORABLE
                edge.reasons.append(f"unfavorable {dow_name} drift ({avg_ret:+.2f}%)")
    except Exception as exc:
        logger.debug("calendar_edge lookup failed for %s: %s", sym, exc)

    # ---------------------------------------------------------------
    # 4) FOMC drift — if today is the day BEFORE an FOMC, apply pre-FOMC drift
    # ---------------------------------------------------------------
    try:
        days_to_fomc = _days_until_next_fomc(today)
        if days_to_fomc == 1:
            from shark.data.knowledge_base import _read_json, _PATTERNS_DIR  # type: ignore
            cal = _read_json(_PATTERNS_DIR / "calendar_effects.json") or {}
            fomc = cal.get("fomc_drift", {}).get("pre_fomc_day", {})
            avg_ret = fomc.get("avg_return_pct", 0)
            if fomc.get("n", 0) >= 5:
                if avg_ret > 0.15:
                    edge.bonus += _BONUS_FOMC_FAVORABLE
                    edge.reasons.append(
                        f"pre-FOMC drift positive ({avg_ret:+.2f}%, n={fomc['n']})"
                    )
                elif avg_ret < -0.15:
                    edge.bonus += _PENALTY_FOMC_UNFAVORABLE
                    edge.reasons.append(
                        f"pre-FOMC drift negative ({avg_ret:+.2f}%, n={fomc['n']})"
                    )
    except Exception as exc:
        logger.debug("fomc edge lookup failed: %s", exc)

    # ---------------------------------------------------------------
    # 5) Compiled lessons — if any recent lesson explicitly mentions this ticker, log it
    # ---------------------------------------------------------------
    try:
        lessons = load_compiled_lessons(limit=5)
        for lesson in lessons:
            txt = (lesson.get("text") or lesson.get("lesson") or "").upper()
            if sym in txt:
                edge.lessons_applied.append(lesson.get("text") or lesson.get("lesson") or "")
    except Exception as exc:
        logger.debug("compiled_lessons lookup failed for %s: %s", sym, exc)

    return edge


def _days_until_next_fomc(today: date) -> int | None:
    """Return number of calendar days until the next FOMC meeting, or None."""
    try:
        from shark.data.macro_calendar import _EVENTS  # type: ignore
    except Exception:
        return None

    soonest: int | None = None
    for ev in _EVENTS:
        ev_type = ev.get("type")
        type_str = ev_type.value if hasattr(ev_type, "value") else str(ev_type)
        if "FOMC" not in type_str.upper():
            continue
        try:
            ev_date = date.fromisoformat(ev.get("date") or "")
        except (TypeError, ValueError):
            continue
        delta = (ev_date - today).days
        if delta < 0:
            continue
        if soonest is None or delta < soonest:
            soonest = delta
    return soonest


def compute_kb_summary() -> dict[str, Any]:
    """Return a high-level KB summary used by the morning briefing email.

    Cold-start safe: returns an empty-ish dict if KB is bare.
    """
    out: dict[str, Any] = {
        "calendar_today": None,
        "fomc_drift": None,
        "leadership": None,
        "anti_patterns_count": 0,
    }

    try:
        from shark.data.knowledge_base import _read_json, _PATTERNS_DIR  # type: ignore
        cal = _read_json(_PATTERNS_DIR / "calendar_effects.json") or {}
        dow_map = cal.get("day_of_week", {})
        today = date.today()
        if today.weekday() < 5:
            dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
            dow_name = dow_names[today.weekday()]
            stats = dow_map.get(dow_name, {})
            if stats:
                out["calendar_today"] = {
                    "day": dow_name,
                    "avg_return_pct": stats.get("avg_return_pct", 0),
                    "win_rate": stats.get("win_rate", 0),
                    "n": stats.get("n", 0),
                }
        out["fomc_drift"] = cal.get("fomc_drift", {}).get("pre_fomc_day")

        sector = _read_json(_PATTERNS_DIR / "sector_rotation.json") or {}
        leadership = sector.get("leadership_ranking", [])[:3]
        if leadership:
            out["leadership"] = leadership

        anti = _read_json(_PATTERNS_DIR / "anti_patterns.json") or {}
        out["anti_patterns_count"] = len(anti)
    except Exception as exc:
        logger.debug("compute_kb_summary partial failure: %s", exc)

    return out
