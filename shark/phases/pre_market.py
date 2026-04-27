from __future__ import annotations
import logging
import re
import subprocess
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions
from shark.data.perplexity import fetch_market_intel
from shark.data.market_regime import detect_regime
from shark.data.relative_strength import compute_relative_strength
from shark.data.macro_calendar import check_macro_calendar
from shark.agents.trade_reviewer import get_recent_lessons, get_pattern_stats
from shark.memory.journal import log_research
from shark.memory import handoff, state

_RESEARCH_LOG = Path(__file__).resolve().parents[2] / "memory" / "RESEARCH-LOG.md"

logger = logging.getLogger(__name__)

_DEFAULT_WATCHLIST = ["NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMD", "PLTR", "TSLA"]
_STRATEGY_PATH = Path(__file__).resolve().parents[2] / "memory" / "TRADING-STRATEGY.md"
_NOTIFY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "notify.sh"


def _read_watchlist() -> list[str]:
    try:
        text = _STRATEGY_PATH.read_text()
    except OSError:
        logger.warning("Could not read TRADING-STRATEGY.md — using default watchlist")
        return _DEFAULT_WATCHLIST

    tickers: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        # Matches "- TICKER" or "- TICKER, TICKER2" (comma-separated on one line)
        bullet = re.match(r"^-\s+([A-Z]{1,5}(?:,\s*[A-Z]{1,5})*)", stripped)
        if bullet:
            for t in re.findall(r"[A-Z]{1,5}", bullet.group(1)):
                tickers.append(t)
            continue
        # Matches "| TICKER |" table rows
        table = re.match(r"^\|\s*([A-Z]{1,5})\s*\|", stripped)
        if table:
            tickers.append(table.group(1))

    seen: set[str] = set()
    unique = [t for t in tickers if not (t in seen or seen.add(t))]
    return unique if unique else _DEFAULT_WATCHLIST


def _score(intel: dict, rs_data: dict | None = None, regime_str: str = "") -> int:
    """Score a ticker based on intel, relative strength, and regime context."""
    score = 0
    catalysts: list[str] = intel.get("catalysts", [])
    sentiment_score: float = float(intel.get("sentiment_score", 0.0))
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

    return score


def _notify_premarket_risk(symbol: str, plpc: float) -> None:
    pct = round(plpc * 100, 2)
    message = f"URGENT: {symbol} is down {pct}% premarket — approaching -7% stop"
    logger.warning(message)
    try:
        subprocess.run(
            [str(_NOTIFY_SCRIPT), "PREMARKET_RISK", symbol, message],
            timeout=15,
            check=False,
        )
    except Exception as exc:
        logger.error("notify.sh failed for %s: %s", symbol, exc)


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

    watchlist = _read_watchlist()
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
    for ticker in watchlist:
        ticker_intel = intel_map.get(ticker, {})
        ticker_rs = rs_map.get(ticker)
        s = _score(ticker_intel, rs_data=ticker_rs, regime_str=regime_str)
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

    bearish_count = sum(1 for _, _, info in scored if float(info.get("sentiment_score", 0.0)) <= -0.3)
    bullish_count = sum(1 for _, _, info in scored if float(info.get("sentiment_score", 0.0)) >= 0.3)
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

    logger.info("pre-market phase complete — %s", decision)
    return True
