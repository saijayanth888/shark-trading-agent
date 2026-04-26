"""
Email Templates — HTML email bodies for all Shark notification types.
"""

_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d0d0d; color: #e8e8e8; margin: 0; padding: 16px; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px;
          padding: 20px; max-width: 640px; margin: 0 auto; }
  .header { background: #111; border-radius: 6px 6px 0 0; padding: 14px 20px;
            margin: -20px -20px 20px; border-bottom: 1px solid #2a2a2a; }
  .header h1 { margin: 0; font-size: 18px; font-weight: 700; color: #fff; }
  .header .sub { font-size: 12px; color: #888; margin-top: 2px; }
  .kv { display: flex; justify-content: space-between; padding: 6px 0;
        border-bottom: 1px solid #222; font-size: 14px; }
  .kv:last-child { border-bottom: none; }
  .label { color: #888; }
  .val { font-weight: 600; }
  .green { color: #22c55e; }
  .red   { color: #ef4444; }
  .yellow{ color: #eab308; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
           font-size: 12px; font-weight: 700; }
  .badge-buy  { background: #14532d; color: #22c55e; }
  .badge-sell { background: #450a0a; color: #ef4444; }
  .badge-hold { background: #1c1917; color: #a8a29e; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }
  th { text-align: left; padding: 6px 8px; color: #888; font-weight: 500;
       border-bottom: 1px solid #2a2a2a; }
  td { padding: 6px 8px; border-bottom: 1px solid #1f1f1f; }
  .alert { background: #450a0a; border: 1px solid #7f1d1d; border-radius: 6px;
           padding: 12px; margin-top: 12px; color: #fca5a5; font-size: 13px; }
  .footer { text-align: center; font-size: 11px; color: #444; margin-top: 16px; }
"""


def _wrap(title: str, subtitle: str, body: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_STYLE}</style></head><body>
<div class="card">
  <div class="header">
    <h1>🦈 {title}</h1>
    <div class="sub">{subtitle}</div>
  </div>
  {body}
  <div class="footer">Shark Trading Agent · paper mode · auto-generated</div>
</div></body></html>"""


def trade_signal_html(
    symbol: str,
    side: str,
    entry,
    stop,
    target,
    rr,
    confidence: float,
    order_id: str,
    thesis: str,
    reasoning: str,
) -> str:
    side_upper = side.upper()
    badge_cls = "badge-buy" if side_upper == "BUY" else "badge-sell"
    conf_pct = f"{confidence:.0%}" if isinstance(confidence, float) and confidence <= 1 else f"{confidence}%"
    body = f"""
    <div class="kv"><span class="label">Signal</span>
      <span class="val"><span class="badge {badge_cls}">{side_upper}</span> {symbol}</span></div>
    <div class="kv"><span class="label">Entry</span><span class="val">${entry}</span></div>
    <div class="kv"><span class="label">Stop</span><span class="val red">${stop}</span></div>
    <div class="kv"><span class="label">Target</span><span class="val green">${target}</span></div>
    <div class="kv"><span class="label">R:R</span><span class="val">{rr}</span></div>
    <div class="kv"><span class="label">Confidence</span><span class="val">{conf_pct}</span></div>
    <div class="kv"><span class="label">Order ID</span><span class="val" style="font-size:12px;color:#888">{order_id}</span></div>
    <div class="kv"><span class="label">Thesis</span><span class="val" style="max-width:320px;text-align:right">{thesis}</span></div>
    <div style="margin-top:12px;font-size:13px;color:#aaa">{reasoning}</div>
    """
    return _wrap(f"Trade Signal — {symbol}", f"{side_upper} signal generated", body)


def daily_summary_html(
    date: str,
    equity: float,
    cash: float,
    day_pnl_dollars: float,
    day_pnl_pct: float,
    positions: list,
    trades_this_week: int,
    circuit_breaker_note: str = "",
) -> str:
    sign = "+" if day_pnl_pct >= 0 else ""
    pnl_cls = "green" if day_pnl_pct >= 0 else "red"

    rows = ""
    for p in positions:
        plpc = float(p.get("unrealized_plpc", 0)) * 100
        plpc_cls = "green" if plpc >= 0 else "red"
        rows += (
            f"<tr><td>{p['symbol']}</td><td>{p['qty']}</td>"
            f"<td>${float(p['current_price']):.2f}</td>"
            f"<td class='{plpc_cls}'>{plpc:+.2f}%</td></tr>"
        )
    pos_table = (
        f"<table><tr><th>Symbol</th><th>Qty</th><th>Price</th><th>P&L%</th></tr>{rows}</table>"
        if rows else "<p style='color:#555;font-size:13px'>No open positions.</p>"
    )

    alert_html = f'<div class="alert">⚠ {circuit_breaker_note}</div>' if circuit_breaker_note else ""

    body = f"""
    <div class="kv"><span class="label">Equity</span><span class="val">${equity:,.2f}</span></div>
    <div class="kv"><span class="label">Cash</span><span class="val">${cash:,.2f}</span></div>
    <div class="kv"><span class="label">Day P&L</span>
      <span class="val {pnl_cls}">{sign}{day_pnl_pct:.2f}% (${day_pnl_dollars:+,.2f})</span></div>
    <div class="kv"><span class="label">Trades this week</span><span class="val">{trades_this_week} / 3</span></div>
    {alert_html}
    <div style="margin-top:16px;font-size:13px;color:#888">Open Positions</div>
    {pos_table}
    """
    return _wrap(f"EOD Report — {date}", f"Daily summary · {date}", body)


def weekly_review_html(
    date: str,
    grade: str,
    week_return_pct: float,
    alpha: float,
    win_rate: float,
    wins: int,
    losses: int,
    profit_factor,
    equity: float,
    closed_trades: list,
    open_positions: list,
    drawdown_note: str = "",
) -> str:
    sign = "+" if week_return_pct >= 0 else ""
    ret_cls = "green" if week_return_pct >= 0 else "red"
    alpha_cls = "green" if alpha >= 0 else "red"
    grade_cls = "green" if grade in ("A", "B") else "yellow" if grade == "C" else "red"
    pf_str = f"{profit_factor:.2f}" if isinstance(profit_factor, float) and profit_factor != float("inf") else "∞"

    trade_rows = ""
    for t in closed_trades:
        trade_rows += (
            f"<tr><td>{t.get('date','')}</td><td>{t.get('symbol','')}</td>"
            f"<td>{t.get('side','')}</td><td>{t.get('qty','')}</td>"
            f"<td>{t.get('price','')}</td><td>{t.get('pl','')}</td></tr>"
        )
    trades_html = (
        f"<div style='margin-top:16px;font-size:13px;color:#888'>Closed Trades</div>"
        f"<table><tr><th>Date</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>P&L</th></tr>"
        f"{trade_rows}</table>"
        if trade_rows else ""
    )

    pos_rows = ""
    for p in open_positions:
        plpc = float(p.get("unrealized_plpc", 0)) * 100
        plpc_cls = "green" if plpc >= 0 else "red"
        pos_rows += (
            f"<tr><td>{p['symbol']}</td><td>{p['qty']}</td>"
            f"<td>${float(p['current_price']):.2f}</td>"
            f"<td class='{plpc_cls}'>{plpc:+.2f}%</td></tr>"
        )
    pos_html = (
        f"<div style='margin-top:16px;font-size:13px;color:#888'>Open Positions</div>"
        f"<table><tr><th>Symbol</th><th>Qty</th><th>Price</th><th>P&L%</th></tr>{pos_rows}</table>"
        if pos_rows else ""
    )

    alert_html = f'<div class="alert">⚠ {drawdown_note}</div>' if drawdown_note else ""

    body = f"""
    <div class="kv"><span class="label">Grade</span>
      <span class="val {grade_cls}" style="font-size:20px">{grade}</span></div>
    <div class="kv"><span class="label">Week Return</span>
      <span class="val {ret_cls}">{sign}{week_return_pct:.2f}%</span></div>
    <div class="kv"><span class="label">Alpha vs S&P 500</span>
      <span class="val {alpha_cls}">{alpha:+.2f}pp</span></div>
    <div class="kv"><span class="label">Win Rate</span>
      <span class="val">{win_rate:.1f}% ({wins}W / {losses}L)</span></div>
    <div class="kv"><span class="label">Profit Factor</span><span class="val">{pf_str}</span></div>
    <div class="kv"><span class="label">Equity</span><span class="val">${equity:,.2f}</span></div>
    {alert_html}
    {trades_html}
    {pos_html}
    """
    return _wrap(f"Weekly Review — {date}", f"Week ending {date} · Grade {grade}", body)


def alert_html(title: str, message: str, severity: str = "warning") -> str:
    """Generic alert email — for midday cuts, thesis breaks, circuit breaker."""
    color = {"warning": "#eab308", "danger": "#ef4444", "info": "#3b82f6"}.get(severity, "#eab308")
    body = f"""
    <div style="border-left:4px solid {color};padding:12px;background:#1f1f1f;border-radius:4px">
      <div style="font-weight:700;color:{color};margin-bottom:6px">{title}</div>
      <div style="font-size:14px;color:#ccc">{message}</div>
    </div>
    """
    return _wrap("Shark Alert", title, body)
