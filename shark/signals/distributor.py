"""
Signal Distributor — sends trade signals and digest emails via SendGrid REST API.

Uses requests (not the sendgrid library) to keep dependencies minimal.
Never raises — all errors are logged and False is returned.
"""

import os
import logging
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

_SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"


# ---------------------------------------------------------------------------
# Core email sender
# ---------------------------------------------------------------------------

def send_email_digest(
    subject: str,
    html_body: str,
    recipient: str | None = None,
) -> bool:
    """
    Send an HTML email via SendGrid REST API.

    Args:
        subject: Email subject line.
        html_body: Full HTML body string.
        recipient: Recipient address. Falls back to NOTIFY_EMAIL env var.

    Returns:
        True if the API accepted the message (2xx), False otherwise.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    sender = os.environ.get("NOTIFY_FROM_EMAIL", "shark@trading.bot")
    to_address = recipient or os.environ.get("NOTIFY_EMAIL", "")

    if not api_key:
        logger.error("SENDGRID_API_KEY is not set — cannot send email.")
        return False

    if not to_address:
        logger.error(
            "No recipient address — set NOTIFY_EMAIL or pass recipient arg."
        )
        return False

    payload = {
        "personalizations": [
            {
                "to": [{"email": to_address}],
                "subject": subject,
            }
        ],
        "from": {"email": sender, "name": "Shark Trading Agent"},
        "content": [{"type": "text/html", "value": html_body}],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            _SENDGRID_API_URL,
            json=payload,
            headers=headers,
            timeout=15,
        )

        if 200 <= response.status_code < 300:
            logger.info(
                "Email sent to %s | subject: '%s' | status: %d",
                to_address,
                subject,
                response.status_code,
            )
            return True
        else:
            logger.error(
                "SendGrid returned %d for '%s': %s",
                response.status_code,
                subject,
                response.text[:400],
            )
            return False

    except requests.RequestException as exc:
        logger.error("SendGrid request error for '%s': %s", subject, exc)
        return False

    except Exception as exc:
        logger.error("Unexpected error sending email '%s': %s", subject, exc)
        return False


# ---------------------------------------------------------------------------
# HTML formatters
# ---------------------------------------------------------------------------

def format_daily_digest(
    trades: list[dict[str, Any]],
    portfolio: dict[str, Any],
    research_summary: str,
) -> str:
    """
    Build a clean HTML daily digest email.

    Sections:
    - Portfolio Summary (equity, cash, day P&L)
    - Today's Trades (table or "No trades today")
    - Market Notes (from research_summary)
    - Open Positions (table)

    Args:
        trades: List of today's trade dicts. Each has: symbol, action, qty,
            price, stop, target, thesis.
        portfolio: Dict with equity, cash, day_pl, positions (list of dicts
            with symbol, qty, current_price, unrealized_pl, unrealized_plpc).
        research_summary: Free-text market notes / research summary for the day.

    Returns:
        HTML string.
    """
    equity = float(portfolio.get("equity", 0))
    cash = float(portfolio.get("cash", 0))
    day_pl = float(portfolio.get("day_pl", 0))
    positions: list[dict] = portfolio.get("positions", [])
    today = datetime.now().strftime("%A, %B %d, %Y")

    day_pl_color = "#27ae60" if day_pl >= 0 else "#e74c3c"
    day_pl_sign = "+" if day_pl >= 0 else ""

    # Build trades table
    if trades:
        trade_rows = ""
        for t in trades:
            trade_rows += (
                f"<tr>"
                f"<td>{t.get('symbol','')}</td>"
                f"<td>{t.get('action','')}</td>"
                f"<td>{t.get('qty','')}</td>"
                f"<td>${float(t.get('price', 0)):.2f}</td>"
                f"<td>${float(t.get('stop', 0)):.2f}</td>"
                f"<td>${float(t.get('target', 0)):.2f}</td>"
                f"<td>{str(t.get('thesis',''))[:60]}</td>"
                f"</tr>"
            )
        trades_section = f"""
        <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;">
            Today's Trades
        </h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
                <tr style="background:#3498db;color:#fff;">
                    <th style="padding:8px;text-align:left;">Symbol</th>
                    <th style="padding:8px;text-align:left;">Action</th>
                    <th style="padding:8px;text-align:left;">Qty</th>
                    <th style="padding:8px;text-align:left;">Price</th>
                    <th style="padding:8px;text-align:left;">Stop</th>
                    <th style="padding:8px;text-align:left;">Target</th>
                    <th style="padding:8px;text-align:left;">Thesis</th>
                </tr>
            </thead>
            <tbody>{trade_rows}</tbody>
        </table>
        """
    else:
        trades_section = """
        <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;">
            Today's Trades
        </h2>
        <p style="color:#7f8c8d;font-style:italic;">No trades today.</p>
        """

    # Build positions table
    if positions:
        pos_rows = ""
        for p in positions:
            pl = float(p.get("unrealized_pl", 0))
            plpc = float(p.get("unrealized_plpc", 0))
            pl_color = "#27ae60" if pl >= 0 else "#e74c3c"
            pos_rows += (
                f"<tr>"
                f"<td style='padding:6px;'>{p.get('symbol','')}</td>"
                f"<td style='padding:6px;'>{p.get('qty','')}</td>"
                f"<td style='padding:6px;'>${float(p.get('current_price',0)):.2f}</td>"
                f"<td style='padding:6px;color:{pl_color};'>"
                f"{'+'if pl>=0 else ''}{pl:.2f} ({plpc*100:+.1f}%)</td>"
                f"</tr>"
            )
        positions_section = f"""
        <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;">
            Open Positions
        </h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
                <tr style="background:#2c3e50;color:#fff;">
                    <th style="padding:8px;text-align:left;">Symbol</th>
                    <th style="padding:8px;text-align:left;">Qty</th>
                    <th style="padding:8px;text-align:left;">Price</th>
                    <th style="padding:8px;text-align:left;">Unrealized P&L</th>
                </tr>
            </thead>
            <tbody>{pos_rows}</tbody>
        </table>
        """
    else:
        positions_section = """
        <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;">
            Open Positions
        </h2>
        <p style="color:#7f8c8d;font-style:italic;">No open positions.</p>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Shark Trading — Daily Digest</title></head>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#2c3e50;">

    <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:20px;border-radius:8px;margin-bottom:24px;">
        <h1 style="color:#f39c12;margin:0;font-size:22px;">🦈 Shark Trading Agent</h1>
        <p style="color:#ecf0f1;margin:6px 0 0;">Daily Digest — {today}</p>
    </div>

    <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;">
        Portfolio Summary
    </h2>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:20px;">
        <tr style="background:#ecf0f1;">
            <td style="padding:10px;font-weight:bold;">Portfolio Value</td>
            <td style="padding:10px;">${equity:,.2f}</td>
        </tr>
        <tr>
            <td style="padding:10px;font-weight:bold;">Cash Available</td>
            <td style="padding:10px;">${cash:,.2f}</td>
        </tr>
        <tr style="background:#ecf0f1;">
            <td style="padding:10px;font-weight:bold;">Day P&L</td>
            <td style="padding:10px;color:{day_pl_color};font-weight:bold;">
                {day_pl_sign}${abs(day_pl):,.2f}
            </td>
        </tr>
    </table>

    {trades_section}

    <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;margin-top:24px;">
        Market Notes
    </h2>
    <p style="line-height:1.6;background:#f8f9fa;padding:12px;border-left:4px solid #3498db;border-radius:4px;">
        {research_summary or "No market notes today."}
    </p>

    {positions_section}

    <hr style="margin-top:30px;border:none;border-top:1px solid #ecf0f1;">
    <p style="color:#95a5a6;font-size:11px;text-align:center;">
        Shark Trading Agent &mdash; Automated intelligence, disciplined execution.<br>
        This is not financial advice.
    </p>
</body>
</html>"""

    return html


def format_weekly_digest(
    weekly_stats: dict[str, Any],
    grade: str,
) -> str:
    """
    Build a clean HTML weekly review email.

    Sections:
    - Week Stats table (trades, wins, losses, total P&L, win rate)
    - Grade (A–F style assessment)
    - What Worked
    - What Didn't
    - Next Week Focus

    Args:
        weekly_stats: Dict with keys: week_label (str), total_trades (int),
            wins (int), losses (int), total_pl (float), win_rate (float),
            what_worked (str), what_didnt (str), next_week_focus (str).
        grade: Letter grade string e.g. "A", "B+", "C-".

    Returns:
        HTML string.
    """
    week_label = weekly_stats.get("week_label", "This Week")
    total_trades = int(weekly_stats.get("total_trades", 0))
    wins = int(weekly_stats.get("wins", 0))
    losses = int(weekly_stats.get("losses", 0))
    total_pl = float(weekly_stats.get("total_pl", 0.0))
    win_rate = float(weekly_stats.get("win_rate", 0.0))
    what_worked = weekly_stats.get("what_worked", "")
    what_didnt = weekly_stats.get("what_didnt", "")
    next_week_focus = weekly_stats.get("next_week_focus", "")

    pl_color = "#27ae60" if total_pl >= 0 else "#e74c3c"
    pl_sign = "+" if total_pl >= 0 else ""

    grade_color = (
        "#27ae60" if grade.startswith("A")
        else "#f39c12" if grade.startswith("B")
        else "#e74c3c"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Shark Trading — Weekly Review</title></head>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#2c3e50;">

    <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:20px;border-radius:8px;margin-bottom:24px;">
        <h1 style="color:#f39c12;margin:0;font-size:22px;">🦈 Shark Trading Agent</h1>
        <p style="color:#ecf0f1;margin:6px 0 0;">Weekly Review — {week_label}</p>
    </div>

    <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;">
        Week Stats
    </h2>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:24px;">
        <tr style="background:#ecf0f1;">
            <td style="padding:10px;font-weight:bold;">Total Trades</td>
            <td style="padding:10px;">{total_trades}</td>
        </tr>
        <tr>
            <td style="padding:10px;font-weight:bold;">Wins</td>
            <td style="padding:10px;color:#27ae60;">{wins}</td>
        </tr>
        <tr style="background:#ecf0f1;">
            <td style="padding:10px;font-weight:bold;">Losses</td>
            <td style="padding:10px;color:#e74c3c;">{losses}</td>
        </tr>
        <tr>
            <td style="padding:10px;font-weight:bold;">Win Rate</td>
            <td style="padding:10px;">{win_rate:.1%}</td>
        </tr>
        <tr style="background:#ecf0f1;">
            <td style="padding:10px;font-weight:bold;">Total P&L</td>
            <td style="padding:10px;color:{pl_color};font-weight:bold;">
                {pl_sign}${abs(total_pl):,.2f}
            </td>
        </tr>
    </table>

    <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:6px;">
        Weekly Grade
    </h2>
    <div style="text-align:center;padding:20px;background:#f8f9fa;border-radius:8px;margin-bottom:24px;">
        <span style="font-size:64px;font-weight:bold;color:{grade_color};">{grade}</span>
    </div>

    <h2 style="color:#2c3e50;border-bottom:2px solid #27ae60;padding-bottom:6px;">
        What Worked
    </h2>
    <p style="line-height:1.6;background:#f0fdf4;padding:12px;border-left:4px solid #27ae60;border-radius:4px;">
        {what_worked or "Nothing to report."}
    </p>

    <h2 style="color:#2c3e50;border-bottom:2px solid #e74c3c;padding-bottom:6px;margin-top:24px;">
        What Didn't Work
    </h2>
    <p style="line-height:1.6;background:#fff5f5;padding:12px;border-left:4px solid #e74c3c;border-radius:4px;">
        {what_didnt or "Nothing to report."}
    </p>

    <h2 style="color:#2c3e50;border-bottom:2px solid #f39c12;padding-bottom:6px;margin-top:24px;">
        Next Week Focus
    </h2>
    <p style="line-height:1.6;background:#fffdf0;padding:12px;border-left:4px solid #f39c12;border-radius:4px;">
        {next_week_focus or "Stay disciplined."}
    </p>

    <hr style="margin-top:30px;border:none;border-top:1px solid #ecf0f1;">
    <p style="color:#95a5a6;font-size:11px;text-align:center;">
        Shark Trading Agent &mdash; Automated intelligence, disciplined execution.<br>
        This is not financial advice.
    </p>
</body>
</html>"""

    return html
