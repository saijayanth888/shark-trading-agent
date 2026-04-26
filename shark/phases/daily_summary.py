import logging
import re
import subprocess
from datetime import date
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions
from shark.memory import handoff, state
from shark.memory.journal import write_daily_summary
from shark.signals.distributor import send_email_digest

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


def _build_positions_table(positions: list[dict]) -> str:
    if not positions:
        return "<p>No open positions.</p>"

    rows = "".join(
        f"<tr><td>{p['symbol']}</td><td>{p['qty']}</td>"
        f"<td>${float(p['current_price']):.2f}</td>"
        f"<td>{float(p.get('unrealized_plpc', 0)) * 100:.2f}%</td></tr>"
        for p in positions
    )
    return (
        "<table border='1' cellpadding='4'>"
        "<tr><th>Symbol</th><th>Qty</th><th>Price</th><th>Unreal. P&L%</th></tr>"
        f"{rows}</table>"
    )


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

    sign = "+" if day_pnl_pct >= 0 else ""
    subject = f"Shark EOD {today} | {sign}{day_pnl_pct:.2f}%"
    if circuit_breaker_active:
        subject += " | ⚠ CIRCUIT BREAKER"

    positions_table = _build_positions_table(positions)
    circuit_status_html = (
        f"<p style='color:red'><strong>{drawdown_note}</strong></p>"
        if circuit_breaker_active
        else "<p>Circuit breaker: OK</p>"
    )

    body_html = f"""
    <h2>Shark EOD Report — {today}</h2>
    <p><strong>Equity:</strong> ${current_equity:,.2f}</p>
    <p><strong>Cash:</strong> ${cash:,.2f}</p>
    <p><strong>Day P&L:</strong> ${day_pnl_dollars:+,.2f} ({sign}{day_pnl_pct:.2f}%)</p>
    <p><strong>Trades this week:</strong> {weekly_count}</p>
    {circuit_status_html}
    <h3>Open Positions</h3>
    {positions_table}
    """

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
