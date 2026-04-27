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


def premarket_briefing_html(
    date: str,
    regime: str,
    macro_impact: str,
    macro_desc: str,
    candidates: list[dict],
    at_risk: list[dict],
    watchlist_size: int,
    bullish_count: int,
    bearish_count: int,
    positions_count: int,
    lessons: list[str] | None = None,
) -> str:
    """Morning briefing email — regime, macro, candidates, at-risk positions."""
    regime_cls = "green" if "BULL" in regime else "red" if "BEAR" in regime else "yellow"
    macro_cls = "red" if macro_impact in ("CRITICAL", "HIGH") else "yellow" if macro_impact == "ELEVATED" else "green"

    cand_rows = ""
    for c in candidates:
        cand_rows += (
            f"<tr><td style='font-weight:600'>{c['symbol']}</td>"
            f"<td>{c['score']}</td>"
            f"<td style='font-size:12px'>{c.get('catalyst', '—')}</td></tr>"
        )
    cand_html = (
        f"<table><tr><th>Symbol</th><th>Score</th><th>Catalyst</th></tr>{cand_rows}</table>"
        if cand_rows else "<p style='color:#555;font-size:13px'>No candidates cleared threshold.</p>"
    )

    risk_rows = ""
    for r in at_risk:
        plpc = float(r.get('unrealized_plpc', 0)) * 100
        risk_rows += (
            f"<tr><td>{r['symbol']}</td>"
            f"<td class='red'>{plpc:+.2f}%</td></tr>"
        )
    risk_html = (
        f'<div class="alert">⚠ At-risk positions</div>'
        f"<table><tr><th>Symbol</th><th>P&L%</th></tr>{risk_rows}</table>"
        if risk_rows else ""
    )

    lessons_html = ""
    if lessons:
        items = "".join(f"<li style='color:#aaa;font-size:12px'>{l}</li>" for l in lessons[:3])
        lessons_html = f"<div style='margin-top:12px;font-size:13px;color:#888'>Recent Lessons</div><ul style='margin:4px 0'>{items}</ul>"

    body = f"""
    <div class="kv"><span class="label">Regime</span>
      <span class="val {regime_cls}">{regime}</span></div>
    <div class="kv"><span class="label">Macro</span>
      <span class="val {macro_cls}">{macro_impact}</span></div>
    <div class="kv"><span class="label">Macro Detail</span>
      <span class="val" style="font-size:12px;max-width:320px;text-align:right">{macro_desc or 'No events'}</span></div>
    <div class="kv"><span class="label">Watchlist</span>
      <span class="val">{watchlist_size} tickers · {bullish_count} bullish · {bearish_count} bearish</span></div>
    <div class="kv"><span class="label">Open Positions</span>
      <span class="val">{positions_count}</span></div>
    {risk_html}
    <div style="margin-top:16px;font-size:13px;color:#888">Candidates for Today</div>
    {cand_html}
    {lessons_html}
    """
    return _wrap(f"Morning Briefing — {date}", f"Pre-market scan · {date}", body)


def backtest_results_html(
    date: str,
    total_return_pct: float,
    total_trades: int,
    win_rate_pct: float,
    sharpe_ratio: float,
    max_drawdown_pct: float,
    profit_factor: float,
    alpha_vs_spy: float | None = None,
    starting_capital: float = 100_000,
    ending_equity: float | None = None,
) -> str:
    """Backtest results summary email."""
    ret_cls = "green" if total_return_pct >= 0 else "red"
    sign = "+" if total_return_pct >= 0 else ""
    sharpe_cls = "green" if sharpe_ratio >= 1.0 else "yellow" if sharpe_ratio >= 0.5 else "red"
    dd_cls = "green" if abs(max_drawdown_pct) <= 10 else "yellow" if abs(max_drawdown_pct) <= 20 else "red"
    pf_str = f"{profit_factor:.2f}" if isinstance(profit_factor, (int, float)) and profit_factor != float("inf") else "∞"
    wr_cls = "green" if win_rate_pct >= 50 else "yellow" if win_rate_pct >= 40 else "red"

    alpha_row = ""
    if alpha_vs_spy is not None:
        alpha_cls = "green" if alpha_vs_spy >= 0 else "red"
        alpha_row = f'<div class="kv"><span class="label">Alpha vs S&P 500</span><span class="val {alpha_cls}">{alpha_vs_spy:+.2f}pp</span></div>'

    equity_row = ""
    if ending_equity is not None:
        equity_row = f'<div class="kv"><span class="label">Ending Equity</span><span class="val">${ending_equity:,.2f}</span></div>'

    body = f"""
    <div class="kv"><span class="label">Total Return</span>
      <span class="val {ret_cls}" style="font-size:18px">{sign}{total_return_pct:.2f}%</span></div>
    <div class="kv"><span class="label">Starting Capital</span><span class="val">${starting_capital:,.0f}</span></div>
    {equity_row}
    {alpha_row}
    <div class="kv"><span class="label">Total Trades</span><span class="val">{total_trades}</span></div>
    <div class="kv"><span class="label">Win Rate</span>
      <span class="val {wr_cls}">{win_rate_pct:.1f}%</span></div>
    <div class="kv"><span class="label">Profit Factor</span><span class="val">{pf_str}</span></div>
    <div class="kv"><span class="label">Sharpe Ratio</span>
      <span class="val {sharpe_cls}">{sharpe_ratio:.2f}</span></div>
    <div class="kv"><span class="label">Max Drawdown</span>
      <span class="val {dd_cls}">{abs(max_drawdown_pct):.2f}%</span></div>
    """
    return _wrap(f"Backtest Results — {date}", f"12-month strategy simulation · {date}", body)


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
