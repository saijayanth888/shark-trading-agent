from __future__ import annotations
import logging
import re
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions
from shark.data.perplexity import fetch_market_intel
from shark.data.market_regime import detect_regime
from shark.data.relative_strength import compute_relative_strength
from shark.data.macro_calendar import check_macro_calendar
from shark.data.watchlist import get_full_watchlist
from shark.agents.trade_reviewer import get_recent_lessons, get_pattern_stats
from shark.memory.journal import log_research
from shark.memory import handoff, state
from shark.signals.distributor import send_email_digest
from shark.signals.templates import premarket_briefing_html, alert_html

_RESEARCH_LOG = Path(__file__).resolve().parents[2] / "memory" / "RESEARCH-LOG.md"

logger = logging.getLogger(__name__)


def _score(
    intel: dict,
    rs_data: dict | None = None,
    regime_str: str = "",
    symbol: str = "",
    today: date | None = None,
) -> tuple[int, "HistoricalEdge | None"]:
    """Score a ticker based on intel, relative strength, regime context, and KB history.

    Returns
    -------
    tuple[int, HistoricalEdge | None]
        (score, historical_edge). historical_edge is None when symbol is empty.
        If historical_edge.reject is True, the caller should treat the ticker
        as auto-rejected regardless of score.
    """
    score = 0
    catalysts: list[str] = intel.get("catalysts", [])
    sentiment_score: float = float(intel.get("sentiment_score") or 0.0)
    analyst_rating: str = intel.get("analyst_rating", "").lower()
    risks: list[str] = intel.get("risks", [])
    earnings_days = intel.get("earnings_within_days")

    catalyst_text = " ".join(catalysts).lower()
    has_specific_catalyst = bool(intel.get("catalyst_specific", False)) or (
        bool(catalysts) and "momentum" not in catalyst_text
    )
    if has_specific_catalyst:
        score += 3
    if sentiment_score >= 0.3:
        score += 2
    if any(word in analyst_rating for word in ("upgrade", "buy", "outperform", "positive")):
        score += 1
    if earnings_days is not None and earnings_days <= 2:
        score -= 3
    if sentiment_score <= -0.3:
        score -= 4

    # Relative Strength bonus (new)
    if rs_data:
        rs_composite = rs_data.get("rs_composite", 0)
        rs_signal = rs_data.get("rs_rank_signal", "")
        if rs_signal == "STRONG_OUTPERFORM":
            score += 3
        elif rs_signal == "OUTPERFORM":
            score += 2
        elif rs_signal == "UNDERPERFORM":
            score -= 2
        elif rs_signal == "STRONG_UNDERPERFORM":
            score -= 3

        if rs_data.get("acceleration", 0) > 0:
            score += 1

    # Regime penalty (new): be pickier in volatile regimes
    if "VOLATILE" in regime_str:
        score -= 1
    if "BEAR" in regime_str:
        score -= 2

    # ========== KB HISTORICAL EDGE (Phase 2) ==========
    # Read-only lookup against kb/patterns/. Cold-start safe (returns 0/no-reject).
    historical_edge = None
    if symbol:
        try:
            from shark.data.kb_scoring import compute_historical_edge
            historical_edge = compute_historical_edge(
                symbol=symbol, regime=regime_str, today=today,
            )
            score += historical_edge.bonus
        except Exception as exc:
            logger.debug("KB historical edge failed for %s: %s", symbol, exc)

    # ========== PEAD — Post-Earnings Announcement Drift (Phase 2.5) ==========
    # Detects qualifying gap+volume events in the recent past and adds a time-
    # decaying bonus across the 60-day drift window.
    if symbol:
        try:
            from shark.data.pead import (
                find_active_pead_setup, compute_pead_score_bonus, save_pead_setup,
            )
            pead_setup = find_active_pead_setup(symbol, today=today)
            if pead_setup is not None:
                pead_bonus = compute_pead_score_bonus(pead_setup)
                if pead_bonus > 0:
                    score += pead_bonus
                    if historical_edge is not None:
                        historical_edge.bonus += pead_bonus
                        historical_edge.reasons.append(
                            f"PEAD active: {pead_setup.direction} gap "
                            f"{pead_setup.gap_pct:+.1f}%, day +{pead_setup.days_since_event} "
                            f"(+{pead_bonus} bonus)"
                        )
                    save_pead_setup(pead_setup)
        except Exception as exc:
            logger.debug("PEAD scoring failed for %s: %s", symbol, exc)

    return score, historical_edge


def _notify_premarket_risk(symbol: str, plpc: float) -> None:
    pct = round(plpc * 100, 2)
    message = f"URGENT: {symbol} is down {pct}% premarket — approaching -7% stop"
    logger.warning(message)
    try:
        html = alert_html(
            title=f"Premarket Risk Alert — {symbol}",
            message=message,
            severity="danger",
        )
        send_email_digest(
            subject=f"Shark PREMARKET RISK: {symbol} at {pct}%",
            body_html=html,
        )
    except Exception as exc:
        logger.error("Premarket risk alert failed for %s: %s", symbol, exc)


def _append_candidate_table(date_str: str, viable: list[tuple[int, str, dict]]) -> None:
    """Append a pipe table of RESEARCH_CANDIDATE rows to today's section in RESEARCH-LOG.md.

    market_open._parse_confirmed_candidates() looks for | SYMBOL | CONFIRMED | rows.
    pre_execute will overwrite these with CONFIRMED/REJECTED after 9:45 AM validation.
    Until then, write RESEARCH_CANDIDATE so market_open has something to parse if
    pre_execute is skipped or fails.
    """
    if not viable:
        return
    try:
        text = _RESEARCH_LOG.read_text(encoding="utf-8") if _RESEARCH_LOG.exists() else ""
    except OSError:
        logger.error("Cannot read RESEARCH-LOG.md for candidate table append")
        return

    table = "\n| Symbol | Status | Score |\n|--------|--------|-------|\n"
    for s, ticker, _ in viable:
        table += f"| {ticker} | RESEARCH_CANDIDATE | {s} |\n"

    # Insert after today's date header if present, otherwise append
    header_match = re.search(rf"^## {re.escape(date_str)}", text, re.MULTILINE)
    if header_match:
        # Find next section or end
        next_section = re.search(r"^## \d{4}-\d{2}-\d{2}", text[header_match.end():], re.MULTILINE)
        if next_section:
            insert_pos = header_match.end() + next_section.start()
            new_text = text[:insert_pos].rstrip() + "\n" + table + "\n\n" + text[insert_pos:]
        else:
            new_text = text.rstrip() + "\n" + table + "\n"
    else:
        new_text = text.rstrip() + f"\n## {date_str}\n" + table + "\n"

    _RESEARCH_LOG.write_text(new_text, encoding="utf-8")
    logger.info("Candidate table written for %s: %s", date_str, [t for _, t, _ in viable])


def run(dry_run: bool = False) -> bool:
    today = date.today().isoformat()
    logger.info("pre-market phase starting — %s (dry_run=%s)", today, dry_run)

    handoff.reset_daily_handoff()

    # === REGIME + MACRO CONTEXT (new) ===
    regime_data = detect_regime()
    regime = regime_data["regime"]
    regime_str = regime.value if hasattr(regime, 'value') else str(regime)
    regime_rules = regime_data["rules"]
    logger.info("Pre-market regime: %s", regime_str)

    macro = check_macro_calendar()
    macro_impact = macro.get("impact_level", "NORMAL")
    logger.info("Pre-market macro: %s — %s", macro_impact, macro.get("description", ""))

    # Load lessons from past trades (new)
    recent_lessons = get_recent_lessons(n=5)
    pattern_stats = get_pattern_stats()
    if recent_lessons:
        logger.info("Recent lessons loaded: %d", len(recent_lessons))

    watchlist = get_full_watchlist()
    logger.info("watchlist: %s", watchlist)

    account = get_account()
    positions = get_positions()

    at_risk = [p for p in positions if float(p.get("unrealized_plpc", 0)) <= -0.06]
    for pos in at_risk:
        _notify_premarket_risk(pos["symbol"], float(pos["unrealized_plpc"]))

    intel_map: dict = fetch_market_intel(watchlist)

    # === RELATIVE STRENGTH RANKING (new) ===
    rs_map: dict = {}
    try:
        for ticker in watchlist:
            rs_data = compute_relative_strength(ticker)
            rs_map[ticker] = rs_data
        logger.info(
            "RS scan complete: outperformers=%s",
            [t for t, rs in rs_map.items() if rs.get("outperforming")],
        )
    except Exception:
        logger.warning("Relative strength scan failed — scoring without RS")

    scored: list[tuple[int, str, dict]] = []
    today_dt = date.today()
    rejected_by_kb: list[tuple[str, str]] = []  # [(ticker, reason)]
    edge_map: dict[str, "HistoricalEdge"] = {}
    for ticker in watchlist:
        ticker_intel = intel_map.get(ticker, {})
        ticker_rs = rs_map.get(ticker)
        s, edge = _score(
            ticker_intel,
            rs_data=ticker_rs,
            regime_str=regime_str,
            symbol=ticker,
            today=today_dt,
        )
        if edge is not None:
            edge_map[ticker] = edge
            if edge.reject:
                rejected_by_kb.append((ticker, "; ".join(edge.reasons[:2])))
                logger.warning("KB anti-pattern HARD-REJECTED %s: %s",
                               ticker, edge.reasons[:1])
                continue  # skip — no historical edge → don't trade
            if edge.bonus != 0:
                logger.info("%s historical edge %+d (%s)",
                            ticker, edge.bonus, "; ".join(edge.reasons[:2]))
        scored.append((s, ticker, ticker_intel))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Regime-adjusted candidate count
    max_candidates = regime_rules.get("max_new_trades_per_day", 3)
    top_n = scored[:max_candidates]

    all_catalysts = [
        item
        for _, ticker, info in scored
        for item in info.get("catalysts", [])
    ]
    all_risks = [
        item
        for _, ticker, info in scored
        for item in info.get("risks", [])
    ] + [
        f"{pos['symbol']} down {round(float(pos['unrealized_plpc'])*100,2)}% premarket"
        for pos in at_risk
    ]

    bearish_count = sum(1 for _, _, info in scored if float(info.get("sentiment_score") or 0.0) <= -0.3)
    bullish_count = sum(1 for _, _, info in scored if float(info.get("sentiment_score") or 0.0) >= 0.3)
    market_context = (
        f"Scanned {len(watchlist)} tickers [regime={regime_str}, macro={macro_impact}]. "
        f"Bullish: {bullish_count}, Bearish: {bearish_count}. "
        f"Top catalyst themes: {'; '.join(dict.fromkeys(all_catalysts[:3]))}"
    )

    # Raise minimum score threshold in bear/volatile regimes
    min_score = 2
    if "BEAR" in regime_str:
        min_score = 4
    elif "VOLATILE" in regime_str:
        min_score = 3

    viable = [(s, ticker, info) for s, ticker, info in top_n if s >= min_score]
    decision = (
        f"RESEARCH_COMPLETE — {len(viable)} candidates (regime={regime_str}, min_score={min_score})"
        if viable
        else f"HOLD — no candidates cleared threshold (min_score={min_score}, regime={regime_str})"
    )

    confirmed_tickers = [t for _, t, _ in viable]
    skipped_tickers = [t for _, t, _ in scored if t not in confirmed_tickers]

    handoff.write_handoff_section("pre-market", {
        "confirmed": ", ".join(confirmed_tickers) if confirmed_tickers else "none",
        "skipped": ", ".join(skipped_tickers[:5]) if skipped_tickers else "none",
        "market": f"bullish={bullish_count} bearish={bearish_count} of {len(watchlist)}",
        "regime": regime_str,
        "macro": macro_impact,
        "lessons": "; ".join(recent_lessons[:3]) if recent_lessons else "none",
    })

    if not dry_run:
        for s, ticker, info in viable:
            ticker_rs = rs_map.get(ticker, {})
            log_research({
                "date": today,
                "symbol": ticker,
                "sentiment_score": info.get("sentiment_score", 0.0),
                "thesis": "; ".join(info.get("catalysts", [])),
                "entry": 0.0,
                "stop": 0.0,
                "target": 0.0,
            })
        _append_candidate_table(today, viable)
        committed = state.commit_memory(
            f"pre-market research {today}: regime={regime_str} macro={macro_impact} "
            f"candidates={','.join(confirmed_tickers) if confirmed_tickers else 'none'}"
        )
        if not committed:
            logger.error("state.commit_memory failed")
            return False
    else:
        logger.info("dry_run — skipping log_research and commit")
        logger.info("market_context=%s decision=%s viable=%s", market_context, decision, [t for _, t, _ in viable])

    # === SEND MORNING BRIEFING EMAIL ===
    try:
        candidates_for_email = [
            {"symbol": t, "score": s, "catalyst": "; ".join(info.get("catalysts", [])[:2]) or "—"}
            for s, t, info in viable
        ]
        body_html = premarket_briefing_html(
            date=today,
            regime=regime_str,
            macro_impact=macro_impact,
            macro_desc=macro.get("description", ""),
            candidates=candidates_for_email,
            at_risk=at_risk,
            watchlist_size=len(watchlist),
            bullish_count=bullish_count,
            bearish_count=bearish_count,
            positions_count=len(positions),
            lessons=recent_lessons[:3] if recent_lessons else None,
        )
        if not dry_run:
            send_email_digest(
                subject=f"Shark Morning Briefing — {today} · {regime_str} · {len(viable)} candidates",
                body_html=body_html,
            )
    except Exception:
        logger.exception("Morning briefing email failed")

    logger.info("pre-market phase complete — %s", decision)
    return True
