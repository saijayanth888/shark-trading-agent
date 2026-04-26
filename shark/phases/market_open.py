import logging
import os
import re
from datetime import date

from shark.data.alpaca_data import get_account, get_positions, get_bars
from shark.data.technical import compute_indicators
from shark.data.perplexity import fetch_market_intel
from shark.agents.analyst_bull import generate_bull_thesis
from shark.agents.analyst_bear import generate_bear_thesis
from shark.execution.guardrails import Guardrails
from shark.agents.decision_arbiter import make_decision
from shark.execution.orders import place_bracket_order
from shark.memory.journal import log_trade
from shark.signals.generator import generate_signal
from shark.signals.distributor import send_email_digest
from shark.memory import state

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RESEARCH_LOG = os.path.join(_REPO_ROOT, "memory", "RESEARCH-LOG.md")
_PROJECT_CONTEXT = os.path.join(_REPO_ROOT, "memory", "PROJECT-CONTEXT.md")

MAX_TRADES_PER_RUN = 3


def _parse_confirmed_candidates(date_str: str) -> list[str]:
    try:
        with open(_RESEARCH_LOG, "r") as f:
            content = f.read()
    except FileNotFoundError:
        logger.warning("RESEARCH-LOG.md not found at %s", _RESEARCH_LOG)
        return []

    # Split into date sections; find the section matching date_str
    sections = re.split(r"(?=^## \d{4}-\d{2}-\d{2})", content, flags=re.MULTILINE)
    target_section = None
    for section in sections:
        if section.startswith(f"## {date_str}"):
            target_section = section
            break

    if not target_section:
        logger.info("No research section found for %s", date_str)
        return []

    symbols: list[str] = []

    # Primary format: pipe-table rows like | SYMBOL | CONFIRMED |
    table_matches = re.findall(
        r"^\|\s*([A-Z]{1,5})\s*\|\s*CONFIRMED\s*\|",
        target_section,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if table_matches:
        symbols = [s.upper() for s in table_matches]
        return symbols

    # Legacy format: "CONFIRMED: AAPL, TSLA" line
    confirmed_line = re.search(
        r"^CONFIRMED:\s*(.+)$", target_section, flags=re.MULTILINE | re.IGNORECASE
    )
    if confirmed_line:
        raw = confirmed_line.group(1)
        symbols = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if re.match(r"^[A-Z]{1,5}$", t.strip().upper())]
        if symbols:
            return symbols

    # Legacy format: "Decision: BUY AAPL" or "**Passed to market-open:** AAPL TSLA"
    passed_line = re.search(
        r"(?:Passed to market-open|Decision)[:\s*]+([A-Z ,]+)",
        target_section,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if passed_line:
        raw = passed_line.group(1)
        symbols = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if re.match(r"^[A-Z]{1,5}$", t.strip().upper())]
        if symbols:
            return symbols

    logger.info("No confirmed candidates parsed for %s", date_str)
    return []


def _is_circuit_breaker_triggered() -> bool:
    try:
        with open(_PROJECT_CONTEXT, "r") as f:
            content = f.read()
        return bool(re.search(r"circuit_breaker_triggered:\s*true", content, re.IGNORECASE))
    except FileNotFoundError:
        logger.warning("PROJECT-CONTEXT.md not found; assuming circuit breaker is not triggered")
        return False


def _build_email_body(signal: dict, decision: dict, execution: dict) -> str:
    symbol = decision.get("symbol", "N/A")
    entry = execution.get("fill_price", decision.get("entry_price", "N/A"))
    stop = execution.get("stop_price", decision.get("stop_loss", "N/A"))
    target = decision.get("target_price", "N/A")
    rr = decision.get("risk_reward_ratio", "N/A")
    reasoning = decision.get("reasoning", "")
    thesis = decision.get("thesis_summary", "")
    confidence = decision.get("confidence", 0)
    order_id = execution.get("order_id", "N/A")

    return (
        f"<h2>Shark Trade Signal — {symbol}</h2>"
        f"<p><b>Decision:</b> BUY</p>"
        f"<p><b>Entry:</b> ${entry} | <b>Stop:</b> ${stop} | <b>Target:</b> ${target} | <b>R:R:</b> {rr}</p>"
        f"<p><b>Confidence:</b> {confidence:.0%}</p>"
        f"<p><b>Order ID:</b> {order_id}</p>"
        f"<p><b>Thesis:</b> {thesis}</p>"
        f"<p><b>Reasoning:</b> {reasoning}</p>"
        f"<p><i>Signal data: {signal}</i></p>"
    )


def run(dry_run: bool = False) -> bool:
    today = date.today().isoformat()
    logger.info("market_open phase starting — date=%s dry_run=%s", today, dry_run)

    if _is_circuit_breaker_triggered():
        logger.info("Circuit breaker triggered — halting all new trades")
        return True

    candidates = _parse_confirmed_candidates(today)
    if not candidates:
        logger.info("No confirmed candidates for %s — nothing to trade", today)
        if not dry_run:
            state.commit_memory(f"market-open {today}: none")
        return True

    logger.info("Confirmed candidates: %s", candidates)

    try:
        account = get_account()
        positions = get_positions()
    except Exception:
        logger.error("Failed to fetch account/positions", exc_info=True)
        return False

    existing_symbols = {p["symbol"].upper() for p in positions}
    weekly_count = state.get_weekly_trade_count()
    peak_equity = state.get_peak_equity()

    account_for_guardrails = {
        "portfolio_value": account["portfolio_value"],
        "cash": account["cash"],
        "positions": positions,
    }

    guardrails = Guardrails()
    symbols_traded: list[str] = []
    trades_placed = 0

    for symbol in candidates:
        if trades_placed >= MAX_TRADES_PER_RUN:
            logger.info("Max trades per run (%d) reached — stopping", MAX_TRADES_PER_RUN)
            break

        if symbol in existing_symbols:
            logger.info("%s already in positions — skipping", symbol)
            continue

        try:
            bars = get_bars(symbol, timeframe="1Day", limit=60)
            technicals = compute_indicators(bars)
            current_price = technicals["current_price"]

            intel = fetch_market_intel([symbol])
            perplexity_intel = intel.get(symbol, {})

            market_data = {**technicals, "bars": bars}

            bull = generate_bull_thesis(symbol, market_data, perplexity_intel)
            bear = generate_bear_thesis(symbol, market_data, perplexity_intel)

            estimated_qty = max(1, int(account["buying_power"] * 0.10 / current_price))
            proposed_trade = {
                "symbol": symbol,
                "qty": estimated_qty,
                "estimated_cost": estimated_qty * current_price,
                "sector": "Technology",
            }

            risk = guardrails.run_all(
                proposed_trade,
                account_for_guardrails,
                weekly_count + trades_placed,
                peak_equity,
                [],
            )

            if not risk["approved"]:
                logger.info(
                    "%s failed guardrails — violations: %s", symbol, risk["violations"]
                )
                continue

            decision = make_decision(bull, bear, risk, technicals)

            if decision["decision"] != "BUY":
                logger.info(
                    "%s decision=%s — skipping", symbol, decision["decision"]
                )
                continue

            qty = risk["adjusted_size"]
            logger.info("%s approved qty=%d entry=%.2f", symbol, qty, current_price)

            if dry_run:
                logger.info("[DRY RUN] Would place bracket order: %s x%d", symbol, qty)
                continue

            execution = place_bracket_order(symbol, qty, trail_pct=10.0)

            fill_price = execution.get("fill_price", current_price)
            stop_price = execution.get("stop_price", decision["stop_loss"])

            log_trade({
                "date": today,
                "symbol": symbol,
                "side": "buy",
                "qty": qty,
                "price": fill_price,
                "stop": stop_price,
                "catalyst": bull.get("catalysts", ""),
                "target": decision.get("target_price", ""),
                "rr": decision.get("risk_reward_ratio", ""),
            })

            signal = generate_signal(decision, execution)
            body_html = _build_email_body(signal, decision, execution)
            send_email_digest(
                subject=f"Shark BUY Signal — {symbol} @ ${fill_price}",
                body_html=body_html,
            )

            symbols_traded.append(symbol)
            trades_placed += 1

        except Exception:
            logger.error("Error processing %s", symbol, exc_info=True)
            continue

    if not dry_run:
        if trades_placed > 0:
            state.update_weekly_trade_count(weekly_count + trades_placed)

        traded_label = ",".join(symbols_traded) if symbols_traded else "none"
        state.commit_memory(f"market-open {today}: {traded_label}")

    logger.info(
        "market_open phase complete — trades_placed=%d symbols=%s",
        trades_placed,
        symbols_traded,
    )
    return True
