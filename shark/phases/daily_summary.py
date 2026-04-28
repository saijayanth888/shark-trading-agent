import logging
import re
import subprocess
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions
from shark.memory import handoff, state
from shark.memory.journal import write_daily_summary
from shark.signals.distributor import send_email_digest
from shark.signals.templates import daily_summary_html

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRADE_LOG_PATH = PROJECT_ROOT / "memory" / "TRADE-LOG.md"

CIRCUIT_BREAKER_THRESHOLD = 0.85
EOD_SNAPSHOT_PATTERN = re.compile(
    r"\*\*Portfolio:\*\*\s+\$([0-9,]+(?:\.[0-9]+)?)"
)


def _parse_yesterday_equity(current_equity: float) -> float:
    if not TRADE_LOG_PATH.exists():
        return current_equity

    try:
        content = TRADE_LOG_PATH.read_text()
        matches = EOD_SNAPSHOT_PATTERN.findall(content)
        if matches:
            raw = matches[-1].replace(",", "")
            return float(raw)
    except Exception:
        logger.exception("Failed to parse yesterday equity from TRADE-LOG.md")

    return current_equity



def run(dry_run: bool = False) -> bool:
    today = date.today().isoformat()

    try:
        account = get_account()
    except Exception:
        logger.exception("get_account failed")
        return False

    try:
        positions = get_positions()
    except Exception:
        logger.exception("get_positions failed")
        positions = []

    current_equity = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))

    yesterday_equity = _parse_yesterday_equity(current_equity)

    day_pnl_dollars = current_equity - yesterday_equity
    day_pnl_pct = (day_pnl_dollars / yesterday_equity * 100) if yesterday_equity > 0 else 0.0

    try:
        state.update_peak_equity(current_equity)
    except Exception:
        logger.exception("update_peak_equity failed")

    circuit_breaker_active = False
    drawdown_note = ""
    try:
        portfolio_state = state.get_portfolio_state()
        peak_equity = float(portfolio_state.get("peak_equity", current_equity))
        if current_equity < peak_equity * CIRCUIT_BREAKER_THRESHOLD:
            drawdown_dollars = peak_equity - current_equity
            drawdown_pct = (drawdown_dollars / peak_equity) * 100
            circuit_breaker_active = True
            drawdown_note = (
                f"CIRCUIT BREAKER TRIGGERED: equity ${current_equity:,.2f} is "
                f"${drawdown_dollars:,.2f} ({drawdown_pct:.1f}%) below peak ${peak_equity:,.2f}"
            )
            logger.warning(drawdown_note)
            if not dry_run:
                state.set_circuit_breaker_triggered(True)
    except Exception:
        logger.exception("Circuit breaker check failed")

    try:
        weekly_count = state.get_weekly_trade_count()
    except Exception:
        logger.exception("get_weekly_trade_count failed")
        weekly_count = 0

    summary = {
        "date": today,
        "equity": current_equity,
        "cash": cash,
        "day_pnl_dollars": day_pnl_dollars,
        "day_pnl_pct": day_pnl_pct,
        "positions": positions,
        "trades_today": 0,
        "trades_this_week": weekly_count,
    }

    try:
        if not dry_run:
            write_daily_summary(summary)
    except Exception:
        logger.exception("write_daily_summary failed")

    # === KB DAILY SNAPSHOT (Phase 2) — append to kb/daily/ for historical record ===
    if not dry_run:
        try:
            from shark.data.knowledge_base import save_daily_snapshot
            save_daily_snapshot(today, summary)
        except Exception as exc:
            logger.debug("KB save_daily_snapshot failed: %s", exc)

    sign = "+" if day_pnl_pct >= 0 else ""
    subject = f"Shark EOD {today} | {sign}{day_pnl_pct:.2f}%"
    if circuit_breaker_active:
        subject += " | ⚠ CIRCUIT BREAKER"

    body_html = daily_summary_html(
        date=today,
        equity=current_equity,
        cash=cash,
        day_pnl_dollars=day_pnl_dollars,
        day_pnl_pct=day_pnl_pct,
        positions=positions,
        trades_this_week=weekly_count,
        circuit_breaker_note=drawdown_note if circuit_breaker_active else "",
    )

    # Append KB-context block — sector leadership + active PEAD setups
    body_html += _kb_context_html()

    try:
        if not dry_run:
            send_email_digest(subject=subject, body_html=body_html)
    except Exception:
        logger.exception("send_email_digest failed")

    handoff.write_handoff_section("daily-summary", {
        "equity": f"${current_equity:,.2f}",
        "cash": f"${cash:,.2f}",
        "day_pnl": f"{sign}{day_pnl_pct:.2f}%",
        "open_positions": str(len(positions)),
        "circuit_breaker": "TRIGGERED" if circuit_breaker_active else "OK",
    })

    # Refresh GitHub Pages dashboard data (best-effort, never blocks commit)
    try:
        if not dry_run:
            from shark.dashboard.generate import generate_dashboard_data
            generate_dashboard_data()
    except Exception:
        logger.exception("Dashboard generation failed — continuing to commit")

    commit_msg = f"EOD snapshot {today} | equity ${current_equity:,.2f} | day {sign}{day_pnl_pct:.2f}%"
    try:
        if not dry_run:
            success = state.commit_memory(commit_msg)
        else:
            success = True

        if not success:
            logger.error("commit_memory returned False — EOD commit failed")
            try:
                send_email_digest(
                    subject=f"Shark ERROR {today}: commit_memory failed",
                    body_html="<p>state.commit_memory() returned False during EOD summary. Manual push required.</p>",
                )
            except Exception:
                logger.exception("Failed to send error email after commit failure")
            return False
    except Exception:
        logger.exception("commit_memory raised an exception")
        return False

    return True


def _kb_context_html() -> str:
    """Render a compact KB-context HTML block: sector leaders + active PEAD setups."""
    parts: list[str] = []

    # Sector leadership (6m momentum, computed weekly)
    try:
        from shark.data.knowledge_base import _read_json, _PATTERNS_DIR
        sector_data = _read_json(_PATTERNS_DIR / "sector_rotation.json") or {}
        top_3 = sector_data.get("top_3_sectors", [])
        bottom_3 = sector_data.get("bottom_3_sectors", [])
        if top_3 and bottom_3:
            parts.append(
                "<p><strong>Sector Leaders (6m):</strong> "
                f"{', '.join(top_3)}<br>"
                "<strong>Sector Laggards (6m):</strong> "
                f"{', '.join(bottom_3)}</p>"
            )
    except Exception as exc:
        logger.debug("kb_context: sector lookup failed: %s", exc)

    # Active PEAD setups
    try:
        from shark.data.knowledge_base import _EARNINGS_DIR
        from datetime import date as _date
        today_d = _date.today()
        active_count = 0
        for path in _EARNINGS_DIR.glob("*_*.json"):
            stem_parts = path.stem.rsplit("_", 1)
            if len(stem_parts) != 2:
                continue
            try:
                event_date = _date.fromisoformat(stem_parts[1])
            except ValueError:
                continue
            days_since = (today_d - event_date).days
            if 1 <= days_since < 60:
                active_count += 1
        if active_count > 0:
            parts.append(
                f"<p><strong>Active PEAD Setups:</strong> {active_count} "
                "(within the 60-day drift window)</p>"
            )
    except Exception as exc:
        logger.debug("kb_context: pead lookup failed: %s", exc)

    if not parts:
        return ""
    return "<hr><h3>Market Context</h3>" + "".join(parts)
