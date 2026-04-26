import logging
import re
from datetime import date, timedelta
from pathlib import Path

from shark.data.alpaca_data import get_account, get_positions
from shark.data.perplexity import fetch_market_intel
from shark.memory import handoff, state
from shark.memory.journal import write_weekly_review
from shark.signals.distributor import send_email_digest

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRADE_LOG_PATH = PROJECT_ROOT / "memory" / "TRADE-LOG.md"

EOD_SNAPSHOT_PATTERN = re.compile(
    r"###\s+([\d]{4}-[\d]{2}-[\d]{2}).*?—\s+EOD Snapshot\s+\|\s+Portfolio:\s+\$([0-9,]+(?:\.[0-9]+)?)"
)
TRADE_ROW_PATTERN = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([A-Z]+)\s*\|\s*([\w\s\(\)]+?)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"
    r"([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|"
)


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _parse_monday_equity(monday: date, current_equity: float) -> float:
    if not TRADE_LOG_PATH.exists():
        return current_equity

    try:
        content = TRADE_LOG_PATH.read_text()
        snapshots = EOD_SNAPSHOT_PATTERN.findall(content)

        last_friday = monday - timedelta(days=3)
        candidates = [
            (date.fromisoformat(d), float(v.replace(",", "")))
            for d, v in snapshots
        ]
        candidates.sort(key=lambda x: x[0])

        for snap_date, equity in reversed(candidates):
            if snap_date < monday:
                return equity
    except Exception:
        logger.exception("Failed to parse Monday equity from TRADE-LOG.md")

    return current_equity


def _parse_week_trades(monday: date) -> tuple[list[dict], list[dict]]:
    closed_trades: list[dict] = []
    open_trades: list[dict] = []

    if not TRADE_LOG_PATH.exists():
        return closed_trades, open_trades

    try:
        content = TRADE_LOG_PATH.read_text()
        for line in content.splitlines():
            if not line.startswith("|"):
                continue

            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) < 5:
                continue

            try:
                row_date = date.fromisoformat(cols[0])
            except ValueError:
                continue

            if row_date < monday:
                continue

            trade = {
                "date": cols[0],
                "symbol": cols[1] if len(cols) > 1 else "",
                "side": cols[2] if len(cols) > 2 else "",
                "qty": cols[3] if len(cols) > 3 else "",
                "price": cols[4] if len(cols) > 4 else "",
                "catalyst": cols[5] if len(cols) > 5 else "",
                "pl": cols[-1] if len(cols) > 6 else "",
            }

            side = trade["side"].upper()
            if "SELL" in side:
                closed_trades.append(trade)
            else:
                open_trades.append(trade)
    except Exception:
        logger.exception("Failed to parse week trades from TRADE-LOG.md")

    return closed_trades, open_trades


def _parse_pl(pl_str: str) -> float:
    try:
        cleaned = pl_str.replace("$", "").replace(",", "").replace("%", "").strip()
        return float(cleaned)
    except (ValueError, AttributeError):
        return 0.0


def _compute_grade(
    week_return_pct: float,
    circuit_breaker_triggered: bool,
    rule_violations: int,
) -> str:
    if circuit_breaker_triggered or rule_violations >= 2:
        return "F"
    if rule_violations == 1 or week_return_pct < -5.0:
        return "D"
    if rule_violations == 0 and week_return_pct > 0:
        return "A"
    if rule_violations == 0 or week_return_pct > 0:
        return "B"
    return "C"


def _build_weekly_email_body(
    today: str,
    stats: dict,
    closed_trades: list[dict],
    open_positions: list[dict],
    grade: str,
    drawdown_note: str,
) -> str:
    trades_html = ""
    if closed_trades:
        rows = "".join(
            f"<tr><td>{t['date']}</td><td>{t['symbol']}</td>"
            f"<td>{t['side']}</td><td>{t['qty']}</td>"
            f"<td>{t['price']}</td><td>{t['pl']}</td></tr>"
            for t in closed_trades
        )
        trades_html = (
            "<h3>Closed Trades This Week</h3>"
            "<table border='1' cellpadding='4'>"
            "<tr><th>Date</th><th>Symbol</th><th>Side</th>"
            "<th>Qty</th><th>Price</th><th>P&L</th></tr>"
            f"{rows}</table>"
        )

    open_html = ""
    if open_positions:
        rows = "".join(
            f"<tr><td>{p['symbol']}</td><td>{p['qty']}</td>"
            f"<td>${float(p['current_price']):.2f}</td>"
            f"<td>{float(p.get('unrealized_plpc', 0)) * 100:.2f}%</td></tr>"
            for p in open_positions
        )
        open_html = (
            "<h3>Open Positions</h3>"
            "<table border='1' cellpadding='4'>"
            "<tr><th>Symbol</th><th>Qty</th><th>Price</th><th>Unreal. P&L%</th></tr>"
            f"{rows}</table>"
        )

    cb_html = (
        f"<p style='color:red'><strong>{drawdown_note}</strong></p>"
        if drawdown_note
        else ""
    )

    return f"""
    <h2>Shark Weekly Review — {today}</h2>
    <h3>Grade: {grade}</h3>
    <p><strong>Week Return:</strong> {stats.get('week_return_pct', 0):+.2f}%</p>
    <p><strong>Alpha vs S&P 500:</strong> {stats.get('alpha', 0):+.2f}%</p>
    <p><strong>Win Rate:</strong> {stats.get('win_rate', 0):.1f}% ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)</p>
    <p><strong>Profit Factor:</strong> {stats.get('profit_factor', 0):.2f}</p>
    <p><strong>Equity:</strong> ${stats.get('current_equity', 0):,.2f}</p>
    {cb_html}
    {trades_html}
    {open_html}
    """


def run(dry_run: bool = False) -> bool:
    today = date.today().isoformat()
    today_date = date.today()
    monday = _monday_of_week(today_date)

    try:
        account = get_account()
    except Exception:
        logger.exception("get_account failed")
        return False

    try:
        open_positions = get_positions()
    except Exception:
        logger.exception("get_positions failed")
        open_positions = []

    current_equity = float(account.get("portfolio_value", 0))

    monday_equity = _parse_monday_equity(monday, current_equity)
    week_return_pct = (
        (current_equity - monday_equity) / monday_equity * 100
        if monday_equity > 0
        else 0.0
    )

    sp500_weekly_pct = 0.0
    sp500_note = ""
    try:
        intel = fetch_market_intel(["SPY"])
        spy_intel = intel.get("SPY", {})
        catalysts_raw = spy_intel.get("catalysts", "")
        match = re.search(r"([+-]?\d+\.?\d*)\s*%", str(catalysts_raw))
        if match:
            sp500_weekly_pct = float(match.group(1))
        else:
            sp500_note = "S&P data unavailable"
            logger.warning("Could not parse SPY weekly return from catalysts: %s", catalysts_raw)
    except Exception:
        sp500_weekly_pct = 0.0
        sp500_note = "S&P data unavailable"
        logger.exception("fetch_market_intel for SPY failed")

    alpha = week_return_pct - sp500_weekly_pct

    closed_trades, open_trades = _parse_week_trades(monday)

    gains = []
    losses = []
    for trade in closed_trades:
        pl = _parse_pl(trade.get("pl", "0"))
        if pl > 0:
            gains.append(pl)
        elif pl < 0:
            losses.append(pl)

    wins = len(gains)
    total_closed = len(closed_trades)
    loss_count = len(losses)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    profit_factor = (
        sum(gains) / abs(sum(losses)) if losses else float("inf")
    )

    circuit_breaker_triggered = False
    drawdown_note = ""
    try:
        portfolio_state = state.get_portfolio_state()
        circuit_breaker_triggered = bool(portfolio_state.get("circuit_breaker_triggered", False))
        if circuit_breaker_triggered:
            peak = float(portfolio_state.get("peak_equity", current_equity))
            drawdown = peak - current_equity
            drawdown_note = (
                f"CIRCUIT BREAKER TRIGGERED: equity ${current_equity:,.2f} "
                f"is ${drawdown:,.2f} below peak ${peak:,.2f}"
            )
            logger.warning(drawdown_note)
    except Exception:
        logger.exception("Failed to read circuit breaker state")

    grade = _compute_grade(week_return_pct, circuit_breaker_triggered, rule_violations=0)

    what_worked = [t["catalyst"] for t in closed_trades if _parse_pl(t.get("pl", "0")) > 0]
    what_didnt = [t["catalyst"] for t in closed_trades if _parse_pl(t.get("pl", "0")) < 0]

    stats = {
        "week_return_pct": week_return_pct,
        "monday_equity": monday_equity,
        "current_equity": current_equity,
        "sp500_weekly_pct": sp500_weekly_pct,
        "sp500_note": sp500_note,
        "alpha": alpha,
        "wins": wins,
        "losses": loss_count,
        "win_rate": win_rate,
        "profit_factor": profit_factor if profit_factor != float("inf") else "inf",
    }

    review = {
        "date": today,
        "stats": stats,
        "closed_trades": closed_trades,
        "open_positions": open_positions,
        "what_worked": what_worked,
        "what_didnt": what_didnt,
        "adjustments": [],
        "grade": grade,
    }

    try:
        if not dry_run:
            write_weekly_review(review)
    except Exception:
        logger.exception("write_weekly_review failed")

    sign = "+" if week_return_pct >= 0 else ""
    subject = (
        f"Shark Weekly Review {today} | Grade {grade} | "
        f"{sign}{week_return_pct:.2f}% | Alpha {alpha:+.2f}%"
    )
    body_html = _build_weekly_email_body(
        today, stats, closed_trades, open_positions, grade, drawdown_note
    )

    try:
        if not dry_run:
            send_email_digest(subject=subject, body_html=body_html)
    except Exception:
        logger.exception("send_email_digest failed")

    handoff.write_handoff_section("weekly-review", {
        "grade": grade,
        "week_return": f"{week_return_pct:+.2f}%",
        "alpha": f"{alpha:+.2f}%",
        "win_rate": f"{win_rate:.1f}% ({wins}W/{loss_count}L)",
    })

    commit_msg = f"weekly review {today} | grade {grade}"
    try:
        if not dry_run:
            success = state.commit_memory(commit_msg)
        else:
            success = True

        if not success:
            logger.error("commit_memory returned False — weekly review commit failed")
            try:
                send_email_digest(
                    subject=f"Shark ERROR {today}: weekly commit_memory failed",
                    body_html="<p>state.commit_memory() returned False during weekly review. Manual push required.</p>",
                )
            except Exception:
                logger.exception("Failed to send error email after weekly commit failure")
            return False
    except Exception:
        logger.exception("commit_memory raised an exception")
        return False

    return True
