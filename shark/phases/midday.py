import logging
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_positions, get_bars
from shark.data.perplexity import fetch_market_intel
from shark.data.technical import compute_indicators
from shark.data.market_regime import detect_regime
from shark.execution.orders import close_position, place_order
from shark.execution.stops import manage_stops
from shark.execution.exit_manager import evaluate_exits, compute_dynamic_stop, check_volatility_expansion
from shark.agents.trade_reviewer import review_closed_trade, save_lesson
from shark.memory import handoff, state
from shark.memory.journal import log_trade
from shark.signals.distributor import send_email_digest
from shark.signals.templates import alert_html

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

HARD_STOP_PCT = -0.07


def run(dry_run: bool = False) -> bool:
    today = date.today().isoformat()
    actions_taken = []

    try:
        positions = get_positions()
    except Exception:
        logger.exception("Failed to fetch positions")
        return False

    if not positions:
        state.commit_memory("midday: no positions")
        return True

    # === REGIME DETECTION (new) — affects stop tightening and exit urgency ===
    regime_data = detect_regime()
    regime = regime_data["regime"]
    regime_str = regime.value if hasattr(regime, 'value') else str(regime)
    logger.info("Midday regime: %s", regime_str)

    cut_symbols = set()
    closed_trades = []  # for post-trade review

    # === PHASE 1: Exit Manager evaluation (new) — multi-reason exit logic ===
    exit_actions = evaluate_exits(positions, trade_log=None, regime=regime_str)

    for action in exit_actions:
        symbol = action["symbol"]
        exit_action = action["action"]
        reason = action["reason"]
        qty_to_close = action.get("qty_to_close", 0)

        if symbol in cut_symbols:
            continue

        try:
            if exit_action == "CLOSE_ALL":
                if not dry_run:
                    result = close_position(symbol)
                    fill_price = result.get("filled_price")
                    qty = result.get("qty", qty_to_close)
                else:
                    fill_price = next((p.get("current_price") for p in positions if p["symbol"] == symbol), 0)
                    qty = qty_to_close

                log_trade({
                    "date": today,
                    "symbol": symbol,
                    "side": f"SELL ({reason[:30]})",
                    "qty": qty,
                    "price": fill_price,
                    "stop": "-",
                    "catalyst": reason,
                    "target": "-",
                    "rr": "-",
                })
                cut_symbols.add(symbol)
                actions_taken.append(f"{symbol}: {reason[:60]}")
                logger.info("Exit manager closed %s: %s", symbol, reason)

                # Post-trade review (new)
                pos_data = next((p for p in positions if p["symbol"] == symbol), {})
                closed_trades.append({
                    "symbol": symbol,
                    "exit_price": fill_price,
                    "pnl_pct": float(pos_data.get("unrealized_plpc", 0)) * 100,
                    "exit_reason": reason[:30],
                })

            elif exit_action == "PARTIAL_SELL":
                tier = action.get("tier", 1)
                if not dry_run:
                    result = place_order(symbol, qty_to_close, "sell")
                    fill_price = result.get("filled_price")
                else:
                    fill_price = next((p.get("current_price") for p in positions if p["symbol"] == symbol), 0)

                log_trade({
                    "date": today,
                    "symbol": symbol,
                    "side": f"SELL (partial T{tier})",
                    "qty": qty_to_close,
                    "price": fill_price,
                    "stop": "-",
                    "catalyst": reason,
                    "target": "-",
                    "rr": "-",
                })
                actions_taken.append(f"{symbol}: partial T{tier} — {qty_to_close} shares")
                logger.info("Partial sell %s tier %d: %d shares — %s", symbol, tier, qty_to_close, reason)

        except Exception:
            logger.exception("Exit manager action failed for %s", symbol)

    # === PHASE 2: Legacy hard stop check (safety net) ===
    for pos in positions:
        symbol = pos["symbol"]
        if symbol in cut_symbols:
            continue
        plpc = float(pos.get("unrealized_plpc", 0.0))

        if plpc <= HARD_STOP_PCT:
            try:
                if not dry_run:
                    result = close_position(symbol)
                    fill_price = result.get("filled_price")
                    qty = result.get("qty", pos["qty"])
                else:
                    fill_price = pos.get("current_price")
                    qty = pos["qty"]

                log_trade({
                    "date": today,
                    "symbol": symbol,
                    "side": "SELL (hard stop)",
                    "qty": qty,
                    "price": fill_price,
                    "stop": "-",
                    "catalyst": "Midday cut: -7% rule triggered",
                    "target": "-",
                    "rr": "-",
                })
                cut_symbols.add(symbol)
                actions_taken.append(f"{symbol}: hard cut at {plpc:.1%}")
                logger.info("Hard cut %s at %.2f%%", symbol, plpc * 100)

                closed_trades.append({
                    "symbol": symbol,
                    "exit_price": fill_price,
                    "pnl_pct": plpc * 100,
                    "exit_reason": "stop-out",
                })
            except Exception:
                logger.exception("Failed to close position for %s", symbol)

    remaining_positions = [p for p in positions if p["symbol"] not in cut_symbols]

    # === PHASE 3: Regime-aware stop management (enhanced) ===
    stop_actions = []
    if remaining_positions:
        try:
            if not dry_run:
                stop_actions = manage_stops(remaining_positions)
            else:
                stop_actions = []

            for action in stop_actions:
                sym = action.get("symbol")
                act = action.get("action")
                new_trail = action.get("new_trail_pct")
                actions_taken.append(f"{sym}: stop tightened to {new_trail}")
                logger.info("Stop tightened for %s — action=%s new_trail_pct=%s", sym, act, new_trail)
        except Exception:
            logger.exception("manage_stops failed")

    # === PHASE 4: Volatility expansion check (new) ===
    for pos in remaining_positions:
        symbol = pos["symbol"]
        try:
            bars = get_bars(symbol, timeframe="1Day", limit=30)
            technicals = compute_indicators(bars)
            current_atr = technicals.get("atr_14", 0)
            # Use 1.5% of price as baseline entry ATR if not tracked
            entry_atr = float(pos.get("current_price", 0)) * 0.015
            vol_check = check_volatility_expansion(symbol, current_atr, entry_atr)
            if vol_check:
                actions_taken.append(f"{symbol}: {vol_check['reason'][:60]}")
                logger.warning("%s volatility expansion: %s", symbol, vol_check["reason"])
        except Exception:
            logger.debug("Vol expansion check skipped for %s", symbol)

    # === PHASE 5: Thesis break check via Perplexity ===
    thesis_break_symbols = set()
    for pos in remaining_positions:
        symbol = pos["symbol"]
        try:
            intel = fetch_market_intel([symbol])
            sym_intel = intel.get(symbol, {})
            sentiment = sym_intel.get("sentiment", "")
            invalidation = sym_intel.get("invalidation_signals", "")

            if sentiment == "bearish" and invalidation:
                reason = invalidation if isinstance(invalidation, str) else str(invalidation)
                qty = pos["qty"]

                if not dry_run:
                    result = close_position(symbol)
                    fill_price = result.get("filled_price")
                    qty = result.get("qty", qty)
                else:
                    fill_price = pos.get("current_price")

                log_trade({
                    "date": today,
                    "symbol": symbol,
                    "side": "SELL (thesis break)",
                    "qty": qty,
                    "price": fill_price,
                    "stop": "-",
                    "catalyst": f"Thesis invalidated: {reason}",
                    "target": "-",
                    "rr": "-",
                })
                thesis_break_symbols.add(symbol)
                actions_taken.append(f"{symbol}: thesis break — {reason}")
                logger.info("Thesis break close for %s: %s", symbol, reason)

                closed_trades.append({
                    "symbol": symbol,
                    "exit_price": fill_price,
                    "pnl_pct": float(pos.get("unrealized_plpc", 0)) * 100,
                    "exit_reason": "thesis-break",
                })
        except Exception:
            logger.exception("Thesis check failed for %s", symbol)

    # === POST-TRADE REVIEW (new) — extract lessons from every closed trade ===
    for trade in closed_trades:
        try:
            review = review_closed_trade(trade, market_context=f"regime={regime_str}")
            save_lesson(trade, review)
            logger.info(
                "Trade review: %s grade=%s pattern=%s lesson=%s",
                trade["symbol"], review.get("grade"), review.get("pattern"),
                review.get("lesson", "")[:60],
            )
        except Exception:
            logger.debug("Trade review failed for %s", trade.get("symbol"))

        # === KB LEDGER (Phase 2) — append to kb/trades/ for historical patterns ===
        try:
            from shark.data.knowledge_base import save_closed_trade
            from shark.memory.open_trades import pop_open_trade
            sidecar = pop_open_trade(trade.get("symbol", "")) or {}
            kb_trade = {
                **trade,
                "ticker": trade.get("symbol"),
                "exit_date": today,
                "regime": regime_str,
                "side": "long",
                "phase": "midday",
                "setup_tag": sidecar.get("setup_tag", "momentum"),
                "pead_event_date": sidecar.get("pead_event_date"),
                "entry_date": sidecar.get("entry_date"),
            }
            save_closed_trade(kb_trade)

            # === PEAD outcome tracking — update the originating setup file ===
            if kb_trade.get("setup_tag") == "pead" and sidecar.get("pead_event_date"):
                try:
                    from shark.data.knowledge_base import _EARNINGS_DIR, _read_json, _write_json
                    setup_path = _EARNINGS_DIR / (
                        f"{trade.get('symbol')}_{sidecar['pead_event_date']}.json"
                    )
                    payload = _read_json(setup_path) or {}
                    payload.setdefault("outcomes", []).append({
                        "entry_date": sidecar.get("entry_date"),
                        "exit_date": today,
                        "pnl_pct": float(trade.get("pnl_pct", 0.0)),
                        "exit_reason": trade.get("exit_reason", "unknown"),
                    })
                    _write_json(setup_path, payload)
                except Exception as exc:
                    logger.debug("PEAD outcome write failed for %s: %s",
                                 trade.get("symbol"), exc)
        except Exception as exc:
            logger.debug("KB save_closed_trade failed for %s: %s",
                         trade.get("symbol"), exc)

    if actions_taken:
        summary = "; ".join(actions_taken)
        try:
            body_html = alert_html(
                title=f"Midday Scan — {today} · Regime: {regime_str}",
                message=(
                    f"Positions closed: {len(cut_symbols)} · "
                    f"Stops tightened: {len(stop_actions)} · "
                    f"Thesis breaks: {len(thesis_break_symbols)}\n\n{summary}"
                ),
                severity="danger" if cut_symbols else "warning",
            )
            send_email_digest(
                subject=f"Shark Midday Alert — {today} · {len(cut_symbols)} exits",
                body_html=body_html,
            )
        except Exception:
            logger.exception("Midday email failed")

    actions_summary = "; ".join(actions_taken) if actions_taken else "no actions"

    handoff.write_handoff_section("midday", {
        "cuts": ", ".join(cut_symbols) if cut_symbols else "none",
        "stops_tightened": str(len(stop_actions)),
        "thesis_breaks": ", ".join(thesis_break_symbols) if thesis_break_symbols else "none",
        "regime": regime_str,
        "actions": actions_summary,
    })

    try:
        state.commit_memory(f"midday scan {today}: {actions_summary}")
    except Exception:
        logger.exception("commit_memory failed")
        return False

    return True
