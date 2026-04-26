import logging
import re
import subprocess
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions
from shark.data.perplexity import fetch_market_intel
from shark.memory.journal import log_research
from shark.memory import state

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
    sentiment: str = intel.get("sentiment", "").lower()
    analyst_rating: str = intel.get("analyst_rating", "").lower()
    risks: list[str] = intel.get("risks", [])

    catalyst_text = " ".join(catalysts).lower()
    has_specific_catalyst = bool(catalysts) and "momentum" not in catalyst_text
    if has_specific_catalyst:
        score += 3
    if sentiment == "bullish":
        score += 2
    if any(word in analyst_rating for word in ("upgrade", "buy", "outperform", "positive")):
        score += 1
    earnings_risk = any(
        word in " ".join(risks).lower() for word in ("earnings today", "earnings tomorrow", "reports today", "reports tomorrow")
    )
    if earnings_risk:
        score -= 3
    if sentiment == "bearish":
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

    trade_ideas = [
        {
            "symbol": ticker,
            "score": s,
            "sentiment": info.get("sentiment"),
            "catalysts": info.get("catalysts", []),
            "risks": info.get("risks", []),
            "analyst_rating": info.get("analyst_rating"),
        }
        for s, ticker, info in top3
    ]

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

    bearish_count = sum(1 for _, _, info in scored if info.get("sentiment", "").lower() == "bearish")
    market_context = (
        f"Scanned {len(watchlist)} tickers. "
        f"Bullish: {sum(1 for _, _, i in scored if i.get('sentiment','').lower()=='bullish')}, "
        f"Bearish: {bearish_count}. "
        f"Top catalyst themes: {'; '.join(dict.fromkeys(all_catalysts[:3]))}"
    )

    has_viable_candidates = any(s >= 2 for s, _, _ in top3)
    decision = (
        f"RESEARCH_COMPLETE — {len(top3)} candidates identified for market-open review"
        if has_viable_candidates
        else "HOLD — no candidates cleared minimum score threshold (>=2)"
    )

    research_data = {
        "date": today,
        "account": account,
        "positions": positions,
        "market_context": market_context,
        "trade_ideas": trade_ideas,
        "risks": all_risks[:10],
        "decision": decision,
    }

    if not dry_run:
        log_research(research_data)
        committed = state.commit_memory(f"pre-market research {today}")
        if not committed:
            logger.error("state.commit_memory failed")
            return False
    else:
        logger.info("dry_run — skipping log_research and commit")
        logger.info("research_data: %s", research_data)

    logger.info("pre-market phase complete — %s", decision)
    return True
