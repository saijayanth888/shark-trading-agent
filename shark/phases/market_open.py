import logging
import os
import re
from datetime import date

from shark.data.alpaca_data import get_account, get_positions, get_bars
from shark.data.technical import compute_indicators
from shark.data.perplexity import fetch_market_intel
from shark.data.market_regime import detect_regime
from shark.data.relative_strength import compute_relative_strength
from shark.data.macro_calendar import check_macro_calendar
from shark.execution.guardrails import Guardrails
from shark.execution.position_sizer import compute_position_size, compute_partial_exit_plan
from shark.agents.combined_analyst import analyze_symbol
from shark.execution.orders import place_bracket_order
from shark.memory.journal import log_trade
from shark.signals.generator import generate_signal
from shark.signals.distributor import send_email_digest
from shark.signals.templates import trade_signal_html
from shark.memory import handoff, state

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RESEARCH_LOG = os.path.join(_REPO_ROOT, "memory", "RESEARCH-LOG.md")
_PROJECT_CONTEXT = os.path.join(_REPO_ROOT, "memory", "PROJECT-CONTEXT.md")

MAX_TRADES_PER_RUN = 3
_EARNINGS_BLOCK_DAYS = 2  # skip if earnings within this many days

# Static ticker → sector mapping (covers the TRADING-STRATEGY.md watchlist)
_TICKER_SECTOR: dict[str, str] = {
    "NVDA": "Technology", "MSFT": "Technology", "AAPL": "Technology",
    "GOOGL": "Technology", "META": "Technology", "AMD": "Technology",
    "AVGO": "Technology", "PLTR": "Technology",
    "JPM": "Financials", "GS": "Financials", "MS": "Financials",
    "UNH": "Healthcare", "LLY": "Healthcare", "JNJ": "Healthcare",
    "XOM": "Energy", "CVX": "Energy",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
}

# Sector → representative ETF for momentum confirmation
_SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Consumer Staples": "XLP",
}


def _check_sector_momentum(sector: str) -> tuple[bool, str]:
    """
    Return (is_bullish, reason) for the sector ETF.

    Bullish = ETF price above its 20-day SMA and RSI > 45.
    Falls back to True (pass-through) if the ETF data cannot be fetched,
    so a Alpaca data outage never blocks all trades.
    """
    etf = _SECTOR_ETFS.get(sector)
    if not etf:
        return True, f"no ETF mapped for sector '{sector}' — skipping momentum check"

    try:
        bars = get_bars(etf, timeframe="1Day", limit=30)
        indicators = compute_indicators(bars)
        price = indicators["current_price"]
        sma20 = indicators.get("sma_20")
        rsi = indicators.get("rsi_14", 50.0)

        if sma20 is None:
            return True, f"{etf} insufficient data for SMA20"

        above_sma = price > sma20
        rsi_ok = rsi > 45.0

        if above_sma and rsi_ok:
            return True, f"{etf} bullish: price ${price:.2f} > SMA20 ${sma20:.2f}, RSI {rsi:.1f}"

        reason = (
            f"{etf} bearish headwind: "
            f"price ${price:.2f} {'>' if above_sma else '<'} SMA20 ${sma20:.2f}, "
            f"RSI {rsi:.1f}"
        )
        return False, reason

    except Exception as exc:
        logger.warning("Sector momentum check failed for %s (%s): %s", sector, etf, exc)
        return True, f"sector momentum check failed for {etf} — defaulting to pass"


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
    return trade_signal_html(
        symbol=decision.get("symbol", "N/A"),
        side="BUY",
        entry=execution.get("fill_price", decision.get("entry_price", "N/A")),
        stop=execution.get("stop_price", decision.get("stop_loss", "N/A")),
        target=decision.get("target_price", "N/A"),
        rr=decision.get("risk_reward_ratio", "N/A"),
        confidence=decision.get("confidence", 0),
        order_id=execution.get("order_id", "N/A"),
        thesis=decision.get("thesis_summary", ""),
        reasoning=decision.get("reasoning", ""),
    )


def run(dry_run: bool = False) -> bool:
    today = date.today().isoformat()
    logger.info("market_open phase starting — date=%s dry_run=%s", today, dry_run)

    if _is_circuit_breaker_triggered():
        logger.info("Circuit breaker triggered — halting all new trades")
        return True

    # === REGIME DETECTION (new) — determines sizing, confidence thresholds ===
    regime_data = detect_regime()
    regime = regime_data["regime"]
    regime_rules = regime_data["rules"]
    logger.info(
        "Market regime: %s — %s",
        regime.value if hasattr(regime, 'value') else regime,
        regime_rules.get("description", ""),
    )

    if not regime_rules.get("new_trades_allowed", True):
        logger.info("Regime %s blocks all new trades — exiting", regime)
        handoff.write_handoff_section("market-open", {
            "traded": "none",
            "reason": f"regime {regime} blocks new longs",
        })
        if not dry_run:
            state.commit_memory(f"market-open {today}: blocked by regime {regime}")
        return True

    # === MACRO CALENDAR CHECK (new) ===
    macro = check_macro_calendar()
    macro_impact = macro.get("impact_level", "NORMAL")
    if macro_impact in ("CRITICAL", "HIGH"):
        logger.info("Macro block: %s — %s", macro_impact, macro.get("description", ""))
        handoff.write_handoff_section("market-open", {
            "traded": "none",
            "reason": f"macro block: {macro.get('description', macro_impact)}",
        })
        if not dry_run:
            state.commit_memory(f"market-open {today}: macro block {macro_impact}")
        return True

    candidates = handoff.get_validated_symbols()
    if not candidates:
        logger.info("No handoff validated symbols — falling back to RESEARCH-LOG.md")
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
    portfolio_value = float(account["portfolio_value"])

    account_for_guardrails = {
        "portfolio_value": portfolio_value,
        "cash": account["cash"],
        "positions": positions,
    }

    # Regime-adjusted max trades per run
    max_trades = min(MAX_TRADES_PER_RUN, regime_rules.get("max_new_trades_per_day", 3))
    regime_mult = regime_rules.get("position_size_multiplier", 1.0)
    macro_mult = macro.get("rules", {}).get("position_size_multiplier", 1.0)

    guardrails = Guardrails()
    symbols_traded: list[str] = []
    trades_placed = 0

    for symbol in candidates:
        if trades_placed >= max_trades:
            logger.info("Max trades per run (%d) reached — stopping", max_trades)
            break

        if symbol in existing_symbols:
            logger.info("%s already in positions — skipping", symbol)
            continue

        try:
            bars = get_bars(symbol, timeframe="1Day", limit=60)
            technicals = compute_indicators(bars)
            current_price = technicals["current_price"]
            momentum_score = technicals.get("momentum_score", 50.0)

            intel = fetch_market_intel([symbol])
            perplexity_intel = intel.get(symbol, {})

            # Gate 1 — Earnings proximity block
            earnings_days = perplexity_intel.get("earnings_within_days")
            if earnings_days is not None and earnings_days <= _EARNINGS_BLOCK_DAYS:
                logger.info(
                    "%s skipped — earnings in %d day(s): never hold through earnings",
                    symbol, earnings_days,
                )
                continue

            # Gate 2 — Catalyst quality: skip if no specific catalyst or already priced in
            if not perplexity_intel.get("catalyst_specific", True):
                logger.info(
                    "%s skipped — no specific catalyst today (vague momentum only)",
                    symbol,
                )
                continue

            if perplexity_intel.get("catalyst_priced_in", False):
                logger.info(
                    "%s skipped — catalyst already priced in (stock already moved on this news)",
                    symbol,
                )
                continue

            # Gate 3 — Sector ETF momentum confirmation
            sector = _TICKER_SECTOR.get(symbol, "Technology")
            sector_ok, sector_reason = _check_sector_momentum(sector)
            if not sector_ok:
                logger.info("%s skipped — %s", symbol, sector_reason)
                continue
            logger.info("%s sector check passed — %s", symbol, sector_reason)

            # Gate 4 (new) — Relative Strength vs SPY
            rs_data = compute_relative_strength(symbol)
            if not rs_data.get("outperforming", False):
                logger.info(
                    "%s skipped — underperforming SPY (RS=%.2f, signal=%s)",
                    symbol, rs_data.get("rs_composite", 0), rs_data.get("rs_rank_signal", "?"),
                )
                continue
            logger.info(
                "%s RS passed — composite=%.2f signal=%s accel=%.2f",
                symbol, rs_data["rs_composite"], rs_data["rs_rank_signal"],
                rs_data.get("acceleration", 0),
            )

            # === ADVANCED POSITION SIZING (new) ===
            regime_str = regime.value if hasattr(regime, 'value') else str(regime)
            atr = technicals.get("atr_14", current_price * 0.02)

            sizing = compute_position_size(
                portfolio_value=portfolio_value,
                current_price=current_price,
                atr=atr,
                regime_multiplier=regime_mult * macro_mult,
                peak_equity=peak_equity,
                confidence=regime_rules.get("confidence_threshold", 0.70),
            )

            if sizing["shares"] <= 0:
                logger.info("%s — position sizer returned 0 shares, skipping", symbol)
                continue

            estimated_qty = sizing["shares"]
            proposed_trade = {
                "symbol": symbol,
                "qty": estimated_qty,
                "estimated_cost": sizing["dollar_amount"],
                "sector": sector,
            }

            risk = guardrails.run_all(
                proposed_trade,
                account_for_guardrails,
                weekly_count + trades_placed,
                peak_equity,
                [],
                regime=regime_str,
                momentum_score=momentum_score,
            )

            if not risk["approved"]:
                logger.info(
                    "%s failed guardrails — violations: %s", symbol, risk["violations"]
                )
                continue

            # Single combined call: bull + bear + decision
            analysis = analyze_symbol(symbol, technicals, bars, perplexity_intel, risk)
            bull = analysis["bull"]
            bear = analysis["bear"]
            decision = analysis["decision"]

            if decision["decision"] != "BUY":
                logger.info(
                    "%s decision=%s — skipping", symbol, decision["decision"]
                )
                continue

            qty = min(risk["adjusted_size"], estimated_qty)
            stop_width = regime_rules.get("stop_width_multiplier", 1.0)
            trail_pct = 10.0 * stop_width

            logger.info(
                "%s APPROVED qty=%d entry=%.2f stop=%.2f trail=%.1f%% | "
                "regime=%s RS=%.2f momentum=%.0f sizing_method=%s",
                symbol, qty, current_price, sizing["stop_price"], trail_pct,
                regime_str, rs_data["rs_composite"], momentum_score,
                sizing["method_used"],
            )

            if dry_run:
                logger.info("[DRY RUN] Would place bracket order: %s x%d", symbol, qty)
                continue

            execution = place_bracket_order(symbol, qty, trail_pct=trail_pct)

            fill_price = execution.get("fill_price", current_price)
            stop_price = execution.get("stop_price", sizing["stop_price"])

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
                "regime": regime_str,
                "rs_composite": rs_data["rs_composite"],
                "momentum_score": momentum_score,
                "sizing_method": sizing["method_used"],
                "atr": atr,
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

    regime_str = regime.value if hasattr(regime, 'value') else str(regime)
    handoff.write_handoff_section("market-open", {
        "traded": ", ".join(symbols_traded) if symbols_traded else "none",
        "count": str(trades_placed),
        "regime": regime_str,
        "macro": macro.get("description", "normal"),
    })

    if not dry_run:
        if trades_placed > 0:
            state.update_weekly_trade_count(weekly_count + trades_placed)

        traded_label = ",".join(symbols_traded) if symbols_traded else "none"
        state.commit_memory(f"market-open {today}: {traded_label} regime={regime_str}")

    logger.info(
        "market_open phase complete — trades=%d symbols=%s regime=%s macro=%s",
        trades_placed, symbols_traded, regime_str, macro_impact,
    )
    return True
