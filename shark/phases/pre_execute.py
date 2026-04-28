from __future__ import annotations
import logging
import re
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions, get_bars
from shark.data.perplexity import fetch_market_intel
from shark.memory import handoff, state

logger = logging.getLogger(__name__)

_RESEARCH_LOG = Path(__file__).resolve().parents[2] / "memory" / "RESEARCH-LOG.md"


def _read_today_candidates() -> list[str]:
    try:
        text = _RESEARCH_LOG.read_text()
    except OSError:
        logger.error("Cannot read RESEARCH-LOG.md")
        return []

    today = date.today().isoformat()
    # Find the section starting with today's date header
    section_pattern = re.compile(
        rf"^##\s+{re.escape(today)}.*?$(.+?)(?=^##\s+\d{{4}}-\d{{2}}-\d{{2}}|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = section_pattern.search(text)
    if not match:
        logger.warning("No section for %s found in RESEARCH-LOG.md", today)
        return []

    section_text = match.group(1)
    # Extract symbols written as **SYMBOL** in the trade_ideas block
    return re.findall(r"\*\*([A-Z]{1,5})\*\*", section_text)


def _get_open_price(bars) -> float | None:
    if bars is None or (hasattr(bars, 'empty') and bars.empty) or len(bars) == 0:
        return None
    if hasattr(bars, 'iloc'):
        return float(bars.iloc[0].get("open", bars.iloc[0].get("o", 0))) or None
    return float(bars[0].get("o", bars[0].get("open", 0))) or None


def _get_latest_price(bars) -> float | None:
    if bars is None or (hasattr(bars, 'empty') and bars.empty) or len(bars) == 0:
        return None
    if hasattr(bars, 'iloc'):
        return float(bars.iloc[-1].get("close", bars.iloc[-1].get("c", 0))) or None
    bar = bars[-1]
    return float(bar.get("c", bar.get("close", 0))) or None


def _total_volume(bars) -> int:
    if bars is None or (hasattr(bars, 'empty') and bars.empty) or len(bars) == 0:
        return 0
    if hasattr(bars, 'iloc'):
        col = "volume" if "volume" in bars.columns else "v"
        if col in bars.columns:
            return int(bars[col].sum())
        return 0
    return sum(int(b.get("v", b.get("volume", 0))) for b in bars)


def _validate_candidate(symbol: str) -> tuple[str, str]:
    """Returns (status, reason) for a single candidate."""
    try:
        bars = get_bars(symbol, timeframe="1Min", limit=30)
    except Exception as exc:
        logger.error("get_bars failed for %s: %s", symbol, exc)
        return "ERROR", f"bars fetch error: {exc}"

    if bars is None or (hasattr(bars, 'empty') and bars.empty) or len(bars) == 0:
        return "HALTED_REJECTED", "No bar data returned — market may be halted"

    total_vol = _total_volume(bars)
    if total_vol == 0:
        return "HALTED_REJECTED", "Zero volume in first 30 minutes — halted or no activity"

    open_price = _get_open_price(bars)
    current_price = _get_latest_price(bars)

    if open_price and current_price and open_price > 0:
        drift = (current_price - open_price) / open_price
        if drift > 0.05:
            return "PRICE_DRIFT_REJECTED", f"Price drift {drift*100:.1f}% above open — entry zone missed"

    try:
        intel = fetch_market_intel([symbol])
        ticker_intel = intel.get(symbol, {})
    except Exception as exc:
        logger.warning("Perplexity check failed for %s: %s — proceeding without news gate", symbol, exc)
        ticker_intel = {}

    sentiment_score = float(ticker_intel.get("sentiment_score", 0.0))
    risks = ticker_intel.get("risks", [])
    new_negative = sentiment_score <= -0.5 or any(
        word in " ".join(risks).lower()
        for word in ("sec investigation", "fraud", "recall", "downgrade", "halt", "delisted")
    )
    if new_negative:
        return "NEWS_REJECTED", f"Breaking negative catalyst — sentiment_score: {sentiment_score:.2f}, risks: {risks[:2]}"

    return "CONFIRMED", "Price in zone, volume ok, thesis intact"


def _build_validation_table(results: list[tuple[str, str, str]]) -> str:
    header = (
        "\n### Pre-Execute Validation — 9:45 AM\n"
        "| Symbol | Status | Reason |\n"
        "|--------|--------|--------|\n"
    )
    rows = "".join(
        f"| {symbol:<6} | {status:<9} | {reason} |\n"
        for symbol, status, reason in results
    )
    return header + rows


def _append_validation_to_log(table: str) -> bool:
    try:
        text = _RESEARCH_LOG.read_text()
    except OSError:
        logger.error("Cannot read RESEARCH-LOG.md for append")
        return False

    today = date.today().isoformat()

    # Don't append twice if already validated today
    if f"Pre-Execute Validation — 9:45 AM" in text and today in text[text.rfind("Pre-Execute"):] if "Pre-Execute" in text else False:
        logger.info("Validation section already present for today — skipping write")
        return False

    # Find the end of today's section to insert before the next date section
    today_header_match = re.search(
        rf"^##\s+{re.escape(today)}",
        text,
        re.MULTILINE,
    )
    if not today_header_match:
        logger.warning("Today's section not found — appending to end of file")
        _RESEARCH_LOG.write_text(text.rstrip() + "\n" + table + "\n")
        return True

    # Find the start of the next section after today's
    next_section = re.search(
        r"^##\s+\d{4}-\d{2}-\d{2}",
        text[today_header_match.end():],
        re.MULTILINE,
    )
    if next_section:
        insert_pos = today_header_match.end() + next_section.start()
        new_text = text[:insert_pos].rstrip() + "\n" + table + "\n\n" + text[insert_pos:]
    else:
        new_text = text.rstrip() + "\n" + table + "\n"

    _RESEARCH_LOG.write_text(new_text)
    return True


def run(dry_run: bool = False) -> bool:
    today = date.today().isoformat()
    logger.info("pre-execute phase starting — %s (dry_run=%s)", today, dry_run)

    candidates = handoff.get_confirmed_symbols()
    if not candidates:
        logger.info("No handoff candidates — falling back to RESEARCH-LOG.md")
        candidates = _read_today_candidates()
    if not candidates:
        logger.warning("No candidates found for today — nothing to validate")
        return True

    logger.info("validating candidates: %s", candidates)

    results: list[tuple[str, str, str]] = []
    for symbol in candidates:
        logger.info("checking %s ...", symbol)
        status, reason = _validate_candidate(symbol)
        logger.info("%s -> %s: %s", symbol, status, reason)
        results.append((symbol, status, reason))

    confirmed = [s for s, status, _ in results if status == "CONFIRMED"]
    rejected = [s for s, status, _ in results if status != "CONFIRMED"]
    logger.info("confirmed: %s | rejected: %s", confirmed, rejected)

    handoff.write_handoff_section("pre-execute", {
        "validated": ", ".join(confirmed) if confirmed else "none",
        "rejected": ", ".join(rejected) if rejected else "none",
    })

    table = _build_validation_table(results)

    if not dry_run:
        modified = _append_validation_to_log(table)
        if modified:
            committed = state.commit_memory(f"pre-execute validation {today}")
            if not committed:
                logger.error("state.commit_memory failed")
                return False
        else:
            logger.info("No file modification — skipping commit")
    else:
        logger.info("dry_run — skipping file write and commit")
        logger.info(table)

    logger.info("pre-execute phase complete")
    return True
