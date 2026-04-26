import logging
import subprocess
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_positions
from shark.data.perplexity import fetch_market_intel
from shark.execution.orders import close_position
from shark.execution.stops import manage_stops
from shark.memory import state
from shark.memory.journal import log_trade

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

    cut_symbols = set()

    for pos in positions:
        symbol = pos["symbol"]
        plpc = pos.get("unrealized_plpc", 0.0)

        if plpc <= HARD_STOP_PCT:
            try:
                if not dry_run:
                    result = close_position(symbol)
                    fill_price = result.get("fill_price")
                    qty = result.get("qty", pos["qty"])
                else:
                    fill_price = pos.get("current_price")
                    qty = pos["qty"]

                log_trade({
                    "date": today,
                    "symbol": symbol,
                    "side": "SELL (stop-out)",
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
            except Exception:
                logger.exception("Failed to close position for %s", symbol)

    remaining_positions = [p for p in positions if p["symbol"] not in cut_symbols]

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
                    fill_price = result.get("fill_price")
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
        except Exception:
            logger.exception("Thesis check failed for %s", symbol)

    if actions_taken:
        summary = "; ".join(actions_taken)
        subject = f"Shark Midday Alert {today}"
        body = (
            f"Midday scan on {today} completed with the following actions: {summary}. "
            f"Positions hard-cut: {len(cut_symbols)}. "
            f"Stops tightened: {len(stop_actions)}. "
            f"Thesis breaks closed: {len(thesis_break_symbols)}."
        )
        try:
            subprocess.run(
                ["bash", "scripts/notify.sh", subject, body],
                cwd=PROJECT_ROOT,
                check=True,
            )
        except Exception:
            logger.exception("notify.sh failed")

    actions_summary = "; ".join(actions_taken) if actions_taken else "no actions"
    try:
        state.commit_memory(f"midday scan {today}: {actions_summary}")
    except Exception:
        logger.exception("commit_memory failed")
        return False

    return True
