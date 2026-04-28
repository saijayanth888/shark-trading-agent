from __future__ import annotations
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions, get_bars
from shark.data.technical import compute_indicators
from shark.data.perplexity import fetch_market_intel
from shark.data.market_regime import detect_regime
from shark.data.relative_strength import compute_relative_strength
from shark.data.macro_calendar import check_macro_calendar
from shark.data.watchlist import get_ticker_sector, SECTOR_ETFS
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
_ANALYSIS_FILE = Path(_REPO_ROOT) / "memory" / "market-open-analysis.json"
_DECISIONS_FILE = Path(_REPO_ROOT) / "memory" / "market-open-decisions.json"

MAX_TRADES_PER_RUN = 3
_EARNINGS_BLOCK_DAYS = 2

# Sector mappings now live in shark.data.watchlist (single source of truth)
# _TICKER_SECTOR → use get_ticker_sector(symbol)
# _SECTOR_ETFS → imported directly


def _check_sector_momentum(sector: str) -> tuple[bool, str]:
    etf = SECTOR_ETFS.get(sector)
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
        return False, (
            f"{etf} bearish headwind: price ${price:.2f} "
            f"{'>' if above_sma else '<'} SMA20 ${sma20:.2f}, RSI {rsi:.1f}"
        )
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
    sections = re.split(r"(?=^## \d{4}-\d{2}-\d{2})", content, flags=re.MULTILINE)
    target_section = None
    for section in sections:
        if section.startswith(f"## {date_str}"):
            target_section = section
            break
    if not target_section:
        return []
    table_matches = re.findall(
        r"^\|\s*([A-Z]{1,5})\s*\|\s*CONFIRMED\s*\|",
        target_section, flags=re.MULTILINE | re.IGNORECASE,
    )
    if table_matches:
        return [s.upper() for s in table_matches]
    confirmed_line = re.search(
        r"^CONFIRMED:\s*(.+)$", target_section, flags=re.MULTILINE | re.IGNORECASE
    )
    if confirmed_line:
        raw = confirmed_line.group(1)
        symbols = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if re.match(r"^[A-Z]{1,5}$", t.strip().upper())]
        if symbols:
            return symbols
    passed_line = re.search(
        r"(?:Passed to market-open|Decision)[:\s*]+([A-Z ,]+)",
        target_section, flags=re.MULTILINE | re.IGNORECASE,
    )
    if passed_line:
        raw = passed_line.group(1)
        symbols = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if re.match(r"^[A-Z]{1,5}$", t.strip().upper())]
        if symbols:
            return symbols
    return []


def _is_circuit_breaker_triggered() -> bool:
    try:
        with open(_PROJECT_CONTEXT, "r") as f:
            content = f.read()
        return bool(re.search(r"circuit_breaker_triggered:\s*true", content, re.IGNORECASE))
    except FileNotFoundError:
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


def _collect_candidate_data(
    symbol: str,
    existing_symbols: set,
    account_for_guardrails: dict,
    portfolio_value: float,
    peak_equity: float,
    regime_str: str,
    regime_rules: dict,
    regime_mult: float,
    macro_mult: float,
    stop_width: float,
    guardrails: Guardrails,
    weekly_count: int,
    candidates_so_far: int,
) -> dict | None:
    """Fetch and validate all data for one symbol. Returns candidate dict or None if blocked."""
    if symbol in existing_symbols:
        logger.info("%s already in positions — skipping", symbol)
        return None
    try:
        bars = get_bars(symbol, timeframe="1Day", limit=60)
        technicals = compute_indicators(bars)
        current_price = technicals["current_price"]
        momentum_score = technicals.get("momentum_score", 50.0)

        intel = fetch_market_intel([symbol])
        perplexity_intel = intel.get(symbol, {})

        earnings_days = perplexity_intel.get("earnings_within_days")
        if earnings_days is not None and earnings_days <= _EARNINGS_BLOCK_DAYS:
            logger.info("%s skipped — earnings in %d day(s)", symbol, earnings_days)
            return None

        if not perplexity_intel.get("catalyst_specific", True):
            logger.info("%s skipped — no specific catalyst", symbol)
            return None

        if perplexity_intel.get("catalyst_priced_in", False):
            logger.info("%s skipped — catalyst already priced in", symbol)
            return None

        sector = get_ticker_sector(symbol)
        sector_ok, sector_reason = _check_sector_momentum(sector)
        if not sector_ok:
            logger.info("%s skipped — %s", symbol, sector_reason)
            return None

        rs_data = compute_relative_strength(symbol)
        if not rs_data.get("outperforming", False):
            logger.info(
                "%s skipped — underperforming SPY (RS=%.2f)",
                symbol, rs_data.get("rs_composite", 0),
            )
            return None

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
            return None

        proposed_trade = {
            "symbol": symbol,
            "qty": sizing["shares"],
            "estimated_cost": sizing["dollar_amount"],
            "sector": sector,
        }
        risk = guardrails.run_all(
            proposed_trade,
            account_for_guardrails,
            weekly_count + candidates_so_far,
            peak_equity,
            [],
            regime=regime_str,
            momentum_score=momentum_score,
        )
        if not risk["approved"]:
            logger.info("%s failed guardrails — %s", symbol, risk["violations"])
            return None

        # Strategy attribution — record what KB signal drove the score.
        setup_tag, pead_event_date = "momentum", None
        try:
            from shark.data.kb_scoring import compute_setup_tag
            setup_tag, pead_event_date = compute_setup_tag(
                symbol=symbol, regime=regime_str,
            )
        except Exception as exc:
            logger.debug("setup_tag computation failed for %s: %s", symbol, exc)

        logger.info("%s passed all gates — including in analysis (tag=%s)", symbol, setup_tag)
        return {
            "symbol": symbol,
            "current_price": round(float(current_price), 2),
            "qty": sizing["shares"],
            "trail_pct": round(10.0 * stop_width, 1),
            "stop_price": round(float(sizing["stop_price"]), 2),
            "sector": sector,
            "sector_reason": sector_reason,
            "setup_tag": setup_tag,
            "pead_event_date": pead_event_date,
            "sizing_method": sizing["method_used"],
            "dollar_amount": round(float(sizing["dollar_amount"]), 2),
            "technicals": {
                "current_price": round(float(current_price), 2),
                "rsi_14": round(float(technicals.get("rsi", technicals.get("rsi_14", 50))), 1),
                "macd_histogram": round(float(technicals.get("macd_histogram", 0)), 4),
                "macd_bullish_cross": technicals.get("macd_bullish_cross", False),
                "bb_squeeze": technicals.get("bb_squeeze", False),
                "adx_14": round(float(technicals.get("adx_14", 0)), 1),
                "sma_20": round(float(technicals.get("sma_20", 0)), 2),
                "sma_50": round(float(technicals.get("sma_50", 0)), 2),
                "volume_ratio": round(float(technicals.get("volume_ratio", 1.0)), 2),
                "momentum_score": round(float(momentum_score), 1),
                "atr_14": round(float(atr), 2),
            },
            "perplexity_intel": perplexity_intel,
            "rs_data": {
                "rs_composite": round(float(rs_data.get("rs_composite", 0)), 3),
                "rs_rank_signal": rs_data.get("rs_rank_signal", "UNKNOWN"),
                "outperforming": rs_data.get("outperforming", False),
                "acceleration": round(float(rs_data.get("acceleration", 0)), 3),
            },
            "risk_check": {
                "approved": risk.get("approved", False),
                "adjusted_size": risk.get("adjusted_size", sizing["shares"]),
                "position_size_pct": round(float(risk.get("position_size_pct", 10)), 1),
            },
        }
    except Exception:
        logger.error("Error collecting data for %s", symbol, exc_info=True)
        return None


def _prepare(dry_run: bool = False) -> bool:
    """
    Cloud routine Step 1: collect all data, write market-open-analysis.json.
    Claude reads this file and writes decisions — no Anthropic API needed.
    """
    today = date.today().isoformat()
    logger.info("market_open PREPARE — date=%s dry_run=%s", today, dry_run)

    def _write_blocked(reason: str) -> bool:
        _ANALYSIS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ANALYSIS_FILE.write_text(json.dumps({"date": today, "blocked": reason, "candidates": []}))
        logger.info("Wrote blocked analysis: %s", reason)
        return True

    if _is_circuit_breaker_triggered():
        return _write_blocked("circuit_breaker")

    regime_data = detect_regime()
    regime = regime_data["regime"]
    regime_rules = regime_data["rules"]
    regime_str = regime.value if hasattr(regime, "value") else str(regime)

    if not regime_rules.get("new_trades_allowed", True):
        handoff.write_handoff_section("market-open", {
            "traded": "none", "reason": f"regime {regime_str} blocks new longs",
        })
        return _write_blocked(f"regime_{regime_str}")

    macro = check_macro_calendar()
    macro_impact = macro.get("impact_level", "NORMAL")
    if macro_impact in ("CRITICAL", "HIGH"):
        handoff.write_handoff_section("market-open", {
            "traded": "none", "reason": f"macro block: {macro.get('description', macro_impact)}",
        })
        return _write_blocked(f"macro_{macro_impact}")

    candidates = handoff.get_validated_symbols()
    if not candidates:
        candidates = _parse_confirmed_candidates(today)
    if not candidates:
        logger.info("No candidates for %s", today)
        _ANALYSIS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ANALYSIS_FILE.write_text(json.dumps({"date": today, "candidates": []}))
        return True

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
    max_trades = min(MAX_TRADES_PER_RUN, regime_rules.get("max_new_trades_per_day", 3))
    regime_mult = regime_rules.get("position_size_multiplier", 1.0)
    macro_mult = macro.get("rules", {}).get("position_size_multiplier", 1.0)
    stop_width = regime_rules.get("stop_width_multiplier", 1.0)

    account_for_guardrails = {
        "portfolio_value": portfolio_value,
        "cash": account["cash"],
        "positions": positions,
    }
    guardrails = Guardrails()
    candidate_data: list[dict] = []

    for symbol in candidates:
        c = _collect_candidate_data(
            symbol=symbol,
            existing_symbols=existing_symbols,
            account_for_guardrails=account_for_guardrails,
            portfolio_value=portfolio_value,
            peak_equity=peak_equity,
            regime_str=regime_str,
            regime_rules=regime_rules,
            regime_mult=regime_mult,
            macro_mult=macro_mult,
            stop_width=stop_width,
            guardrails=guardrails,
            weekly_count=weekly_count,
            candidates_so_far=len(candidate_data),
        )
        if c:
            candidate_data.append(c)

    analysis = {
        "date": today,
        "regime": regime_str,
        "macro_impact": macro_impact,
        "macro_description": macro.get("description", "normal"),
        "portfolio_value": round(portfolio_value, 2),
        "peak_equity": round(float(peak_equity), 2) if peak_equity else None,
        "weekly_trade_count": weekly_count,
        "max_trades_remaining": max_trades,
        "candidates": candidate_data,
    }

    _ANALYSIS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ANALYSIS_FILE.write_text(json.dumps(analysis, indent=2))
    logger.info(
        "Analysis written: %d candidates — %s",
        len(candidate_data), str(_ANALYSIS_FILE),
    )
    return True


def _execute(dry_run: bool = False) -> bool:
    """
    Cloud routine Step 3: read Claude's decisions, place orders, commit memory.
    Claude wrote memory/market-open-decisions.json in Step 2.
    """
    today = date.today().isoformat()
    logger.info("market_open EXECUTE — date=%s dry_run=%s", today, dry_run)

    if not _DECISIONS_FILE.exists():
        logger.error("Decisions file not found: %s", _DECISIONS_FILE)
        return False
    if not _ANALYSIS_FILE.exists():
        logger.error("Analysis file not found: %s", _ANALYSIS_FILE)
        return False

    try:
        decisions_data = json.loads(_DECISIONS_FILE.read_text())
        analysis_data = json.loads(_ANALYSIS_FILE.read_text())
    except Exception:
        logger.error("Failed to read decisions/analysis files", exc_info=True)
        return False

    candidate_map = {c["symbol"]: c for c in analysis_data.get("candidates", [])}
    regime_str = analysis_data.get("regime", "UNKNOWN")
    weekly_count = state.get_weekly_trade_count()
    max_trades = analysis_data.get("max_trades_remaining", MAX_TRADES_PER_RUN)
    symbols_traded: list[str] = []
    trades_placed = 0

    for dec in decisions_data.get("decisions", []):
        if trades_placed >= max_trades:
            logger.info("Max trades reached (%d) — stopping", max_trades)
            break

        symbol = dec.get("symbol", "")
        decision = dec.get("decision", "NO_TRADE")

        if decision != "BUY":
            logger.info("%s — Claude decided %s", symbol, decision)
            continue

        candidate = candidate_map.get(symbol)
        if not candidate:
            logger.warning("%s — no matching candidate data, skipping", symbol)
            continue

        qty = candidate["risk_check"].get("adjusted_size", candidate["qty"])
        trail_pct = candidate["trail_pct"]
        current_price = candidate["current_price"]
        atr = candidate["technicals"]["atr_14"]

        logger.info(
            "%s EXECUTE qty=%d trail=%.1f%% confidence=%.2f rr=%.1f",
            symbol, qty, trail_pct,
            dec.get("confidence", 0), dec.get("risk_reward_ratio", 0),
        )

        if dry_run:
            logger.info("[DRY RUN] Would place bracket order: %s x%d", symbol, qty)
            continue

        try:
            execution = place_bracket_order(symbol, qty, trail_pct=trail_pct)
        except Exception:
            logger.error("Failed to place order for %s", symbol, exc_info=True)
            continue

        fill_price = execution.get("fill_price", current_price)
        stop_price = execution.get("stop_price", dec.get("stop_loss", candidate["stop_price"]))

        log_trade({
            "date": today,
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "price": fill_price,
            "stop": stop_price,
            "catalyst": dec.get("bull_thesis", ""),
            "target": dec.get("target_price", ""),
            "rr": dec.get("risk_reward_ratio", ""),
            "regime": regime_str,
            "rs_composite": candidate["rs_data"]["rs_composite"],
            "momentum_score": candidate["technicals"]["momentum_score"],
            "sizing_method": candidate["sizing_method"],
            "atr": atr,
        })

        # Strategy-attribution sidecar — read by midday at close.
        try:
            from shark.memory.open_trades import upsert_open_trade
            upsert_open_trade(
                symbol,
                setup_tag=candidate.get("setup_tag", "momentum"),
                pead_event_date=candidate.get("pead_event_date"),
                entry_date=today,
                entry_price=float(fill_price),
                regime=regime_str,
            )
        except Exception as exc:
            logger.debug("upsert_open_trade failed for %s: %s", symbol, exc)

        signal = generate_signal(dec, execution)
        body_html = _build_email_body(signal, dec, execution)
        send_email_digest(
            subject=f"Shark BUY Signal — {symbol} @ ${fill_price}",
            body_html=body_html,
        )
        symbols_traded.append(symbol)
        trades_placed += 1

    handoff.write_handoff_section("market-open", {
        "traded": ", ".join(symbols_traded) if symbols_traded else "none",
        "count": str(trades_placed),
        "regime": regime_str,
        "macro": analysis_data.get("macro_description", "normal"),
    })

    if not dry_run:
        if trades_placed > 0:
            state.update_weekly_trade_count(weekly_count + trades_placed)
        traded_label = ",".join(symbols_traded) if symbols_traded else "none"
        state.commit_memory(f"market-open {today}: {traded_label} regime={regime_str}")
        # Clean up ephemeral files — don't leave stale decisions for the next run
        _DECISIONS_FILE.unlink(missing_ok=True)
        _ANALYSIS_FILE.unlink(missing_ok=True)

    logger.info(
        "market_open EXECUTE complete — trades=%d symbols=%s",
        trades_placed, symbols_traded,
    )
    return True


def _run_full(dry_run: bool = False) -> bool:
    """
    Local dev path: full pipeline with combined_analyst (uses ANTHROPIC_API_KEY if set,
    falls back to rule-based if not).
    """
    today = date.today().isoformat()
    logger.info("market_open FULL — date=%s dry_run=%s", today, dry_run)

    if _is_circuit_breaker_triggered():
        logger.info("Circuit breaker triggered — halting all new trades")
        return True

    regime_data = detect_regime()
    regime = regime_data["regime"]
    regime_rules = regime_data["rules"]
    regime_str = regime.value if hasattr(regime, "value") else str(regime)
    logger.info("Market regime: %s — %s", regime_str, regime_rules.get("description", ""))

    if not regime_rules.get("new_trades_allowed", True):
        logger.info("Regime %s blocks all new trades — exiting", regime_str)
        handoff.write_handoff_section("market-open", {
            "traded": "none", "reason": f"regime {regime_str} blocks new longs",
        })
        if not dry_run:
            state.commit_memory(f"market-open {today}: blocked by regime {regime_str}")
        return True

    macro = check_macro_calendar()
    macro_impact = macro.get("impact_level", "NORMAL")
    if macro_impact in ("CRITICAL", "HIGH"):
        logger.info("Macro block: %s — %s", macro_impact, macro.get("description", ""))
        handoff.write_handoff_section("market-open", {
            "traded": "none", "reason": f"macro block: {macro.get('description', macro_impact)}",
        })
        if not dry_run:
            state.commit_memory(f"market-open {today}: macro block {macro_impact}")
        return True

    candidates = handoff.get_validated_symbols()
    if not candidates:
        candidates = _parse_confirmed_candidates(today)
    if not candidates:
        logger.info("No confirmed candidates for %s", today)
        if not dry_run:
            state.commit_memory(f"market-open {today}: none")
        return True

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
    max_trades = min(MAX_TRADES_PER_RUN, regime_rules.get("max_new_trades_per_day", 3))
    regime_mult = regime_rules.get("position_size_multiplier", 1.0)
    macro_mult = macro.get("rules", {}).get("position_size_multiplier", 1.0)
    stop_width = regime_rules.get("stop_width_multiplier", 1.0)

    account_for_guardrails = {
        "portfolio_value": portfolio_value,
        "cash": account["cash"],
        "positions": positions,
    }
    guardrails = Guardrails()
    symbols_traded: list[str] = []
    trades_placed = 0

    for symbol in candidates:
        if trades_placed >= max_trades:
            break
        c = _collect_candidate_data(
            symbol=symbol,
            existing_symbols=existing_symbols,
            account_for_guardrails=account_for_guardrails,
            portfolio_value=portfolio_value,
            peak_equity=peak_equity,
            regime_str=regime_str,
            regime_rules=regime_rules,
            regime_mult=regime_mult,
            macro_mult=macro_mult,
            stop_width=stop_width,
            guardrails=guardrails,
            weekly_count=weekly_count,
            candidates_so_far=trades_placed,
        )
        if not c:
            continue

        # Reconstruct full technicals/bars for combined_analyst
        try:
            bars = get_bars(symbol, timeframe="1Day", limit=60)
            technicals = compute_indicators(bars)
        except Exception:
            logger.error("Error re-fetching bars for %s", symbol, exc_info=True)
            continue

        risk = {
            "approved": True,
            "adjusted_size": c["risk_check"]["adjusted_size"],
            "position_size_pct": c["risk_check"]["position_size_pct"],
            "violations": [],
        }

        analysis = analyze_symbol(symbol, technicals, bars, c["perplexity_intel"], risk)
        decision = analysis["decision"]

        if decision["decision"] != "BUY":
            logger.info("%s decision=%s — skipping", symbol, decision["decision"])
            continue

        qty = c["risk_check"]["adjusted_size"]
        trail_pct = c["trail_pct"]
        current_price = c["current_price"]

        logger.info(
            "%s APPROVED qty=%d entry=%.2f trail=%.1f%% regime=%s RS=%.2f",
            symbol, qty, current_price, trail_pct, regime_str, c["rs_data"]["rs_composite"],
        )

        if dry_run:
            logger.info("[DRY RUN] Would place bracket order: %s x%d", symbol, qty)
            continue

        execution = place_bracket_order(symbol, qty, trail_pct=trail_pct)
        fill_price = execution.get("fill_price", current_price)
        stop_price = execution.get("stop_price", c["stop_price"])

        log_trade({
            "date": today,
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "price": fill_price,
            "stop": stop_price,
            "catalyst": analysis["bull"].get("catalysts", ""),
            "target": decision.get("target_price", ""),
            "rr": decision.get("risk_reward_ratio", ""),
            "regime": regime_str,
            "rs_composite": c["rs_data"]["rs_composite"],
            "momentum_score": c["technicals"]["momentum_score"],
            "sizing_method": c["sizing_method"],
            "atr": c["technicals"]["atr_14"],
        })

        signal = generate_signal(decision, execution)
        body_html = _build_email_body(signal, decision, execution)
        send_email_digest(
            subject=f"Shark BUY Signal — {symbol} @ ${fill_price}",
            body_html=body_html,
        )
        symbols_traded.append(symbol)
        trades_placed += 1

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
        "market_open FULL complete — trades=%d symbols=%s regime=%s",
        trades_placed, symbols_traded, regime_str,
    )
    return True


def run(dry_run: bool = False, mode: str = "full") -> bool:
    if mode == "prepare":
        return _prepare(dry_run)
    elif mode == "execute":
        return _execute(dry_run)
    else:
        return _run_full(dry_run)
