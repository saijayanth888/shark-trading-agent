import logging
import re
import subprocess
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions
from shark.data.perplexity import fetch_market_intel
from shark.memory.journal import log_research
from shark.memory import state

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


def _score(intel: dict) -> int:
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

    watchlist = _read_watchlist()
    logger.info("watchlist: %s", watchlist)

    account = get_account()
    positions = get_positions()

    at_risk = [p for p in positions if float(p.get("unrealized_plpc", 0)) <= -0.06]
    for pos in at_risk:
        _notify_premarket_risk(pos["symbol"], float(pos["unrealized_plpc"]))

    intel_map: dict = fetch_market_intel(watchlist)

    scored: list[tuple[int, str, dict]] = []
    for ticker in watchlist:
        ticker_intel = intel_map.get(ticker, {})
        s = _score(ticker_intel)
        scored.append((s, ticker, ticker_intel))

    scored.sort(key=lambda x: x[0], reverse=True)
    top3 = scored[:3]

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
        f"Scanned {len(watchlist)} tickers. "
        f"Bullish: {bullish_count}, Bearish: {bearish_count}. "
        f"Top catalyst themes: {'; '.join(dict.fromkeys(all_catalysts[:3]))}"
    )

    viable = [(s, ticker, info) for s, ticker, info in top3 if s >= 2]
    decision = (
        f"RESEARCH_COMPLETE — {len(viable)} candidates identified for market-open review"
        if viable
        else "HOLD — no candidates cleared minimum score threshold (>=2)"
    )

    if not dry_run:
        # Write one research entry per viable candidate so pre_execute and market_open can read them
        for s, ticker, info in viable:
            log_research({
                "date": today,
                "symbol": ticker,
                "sentiment_score": info.get("sentiment_score", 0.0),
                "thesis": "; ".join(info.get("catalysts", [])),
                "entry": 0.0,
                "stop": 0.0,
                "target": 0.0,
            })
        # Append a pipe table so market_open._parse_confirmed_candidates() can find them
        _append_candidate_table(today, viable)
        committed = state.commit_memory(f"pre-market research {today}")
        if not committed:
            logger.error("state.commit_memory failed")
            return False
    else:
        logger.info("dry_run — skipping log_research and commit")
        logger.info("market_context=%s decision=%s viable=%s", market_context, decision, [t for _, t, _ in viable])

    logger.info("pre-market phase complete — %s", decision)
    return True
