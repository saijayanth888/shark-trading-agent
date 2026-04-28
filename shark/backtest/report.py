"""
shark/backtest/report.py
--------------------------
Generate a markdown report from backtest metrics and write to
memory/BACKTEST-REPORT.md for consumption by weekly-review and
the context management system.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MEMORY_DIR = Path(__file__).resolve().parents[2] / "memory"
_REPORT_PATH = _MEMORY_DIR / "BACKTEST-REPORT.md"


def generate_report(metrics: dict[str, Any]) -> Path:
    """Generate BACKTEST-REPORT.md from metrics dict. Returns path written."""
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    _header(lines, metrics)
    _summary(lines, metrics)
    _trade_stats(lines, metrics)
    _risk_metrics(lines, metrics)
    _regime_breakdown(lines, metrics)
    _setup_tag_breakdown(lines, metrics)
    _exit_breakdown(lines, metrics)
    _monthly_returns(lines, metrics)
    _notable_trades(lines, metrics)
    _parameters(lines, metrics)
    _recommendations(lines, metrics)

    content = "\n".join(lines) + "\n"
    _REPORT_PATH.write_text(content, encoding="utf-8")
    logger.info("Backtest report written to %s (%d lines)", _REPORT_PATH, len(lines))

    return _REPORT_PATH


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _header(lines: list[str], metrics: dict) -> None:
    lines.append("# Backtest Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    params = metrics.get("parameters", {})
    lines.append(f"- **Capital**: ${params.get('starting_capital', 0):,.0f}")
    lines.append(f"- **Symbols tested**: {params.get('symbols_tested', 0)}")
    lines.append(f"- **Simulation days**: {params.get('simulation_days', 0)}")
    lines.append("")


def _summary(lines: list[str], metrics: dict) -> None:
    s = metrics.get("summary", {})
    lines.append("## Performance Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Starting Capital | ${s.get('starting_capital', 0):,.2f} |")
    lines.append(f"| Ending Capital | ${s.get('ending_capital', 0):,.2f} |")
    lines.append(f"| Total Return | {s.get('total_return_pct', 0):+.2f}% |")
    lines.append(f"| Total P&L | ${s.get('total_pl', 0):+,.2f} |")
    lines.append(f"| CAGR | {s.get('cagr_pct', 0):.2f}% |")
    lines.append("")


def _trade_stats(lines: list[str], metrics: dict) -> None:
    t = metrics.get("trade_stats", {})
    lines.append("## Trade Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total Trades | {t.get('total_trades', 0)} |")
    lines.append(f"| Winners | {t.get('winners', 0)} |")
    lines.append(f"| Losers | {t.get('losers', 0)} |")
    lines.append(f"| Win Rate | {t.get('win_rate_pct', 0):.1f}% |")
    lines.append(f"| Profit Factor | {t.get('profit_factor', 0):.2f} |")
    lines.append(f"| Avg Winner | ${t.get('avg_winner', 0):+,.2f} ({t.get('avg_win_pct', 0):+.2f}%) |")
    lines.append(f"| Avg Loser | ${t.get('avg_loser', 0):,.2f} ({t.get('avg_loss_pct', 0):.2f}%) |")
    lines.append(f"| Win/Loss Ratio | {t.get('win_loss_ratio', 0):.2f} |")
    lines.append(f"| Expectancy | ${t.get('expectancy', 0):+,.2f}/trade |")
    lines.append("")


def _risk_metrics(lines: list[str], metrics: dict) -> None:
    r = metrics.get("risk_metrics", {})
    h = metrics.get("holding_period", {})
    lines.append("## Risk Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Max Drawdown | {r.get('max_drawdown_pct', 0):.2f}% |")
    lines.append(f"| Sharpe Ratio | {r.get('sharpe_ratio', 0):.2f} |")
    lines.append(f"| Sortino Ratio | {r.get('sortino_ratio', 0):.2f} |")
    lines.append(f"| Max Consecutive Wins | {r.get('max_consecutive_wins', 0)} |")
    lines.append(f"| Max Consecutive Losses | {r.get('max_consecutive_losses', 0)} |")
    lines.append(f"| Avg Hold (all) | {h.get('avg_days', 0):.1f} days |")
    lines.append(f"| Avg Hold (winners) | {h.get('avg_days_winners', 0):.1f} days |")
    lines.append(f"| Avg Hold (losers) | {h.get('avg_days_losers', 0):.1f} days |")
    lines.append("")


def _regime_breakdown(lines: list[str], metrics: dict) -> None:
    regimes = metrics.get("regime_breakdown", {})
    if not regimes:
        return

    lines.append("## Regime Breakdown")
    lines.append("")
    lines.append("| Regime | Trades | Total P&L | Win Rate |")
    lines.append("|---|---|---|---|")
    for regime, stats in sorted(regimes.items()):
        lines.append(
            f"| {regime} | {stats['trades']} | ${stats['total_pl']:+,.2f} | {stats['win_rate_pct']:.1f}% |"
        )
    lines.append("")


def _setup_tag_breakdown(lines: list[str], metrics: dict) -> None:
    tags = metrics.get("setup_tag_breakdown", {})
    if not tags:
        return

    lines.append("## Strategy Breakdown (setup_tag)")
    lines.append("")
    lines.append("| Setup | Trades | Total P&L | Win Rate | Avg P&L |")
    lines.append("|---|---|---|---|---|")
    for tag, stats in sorted(tags.items(), key=lambda x: x[1]["total_pl"], reverse=True):
        lines.append(
            f"| {tag} | {stats['trades']} | ${stats['total_pl']:+,.2f} | "
            f"{stats['win_rate_pct']:.1f}% | ${stats['avg_pl']:+,.2f} |"
        )
    lines.append("")


def _exit_breakdown(lines: list[str], metrics: dict) -> None:
    exits = metrics.get("exit_breakdown", {})
    if not exits:
        return

    lines.append("## Exit Reason Breakdown")
    lines.append("")
    lines.append("| Exit Reason | Count | Total P&L | Avg P&L |")
    lines.append("|---|---|---|---|")
    for reason, stats in sorted(exits.items(), key=lambda x: x[1]["total_pl"], reverse=True):
        lines.append(
            f"| {reason} | {stats['count']} | ${stats['total_pl']:+,.2f} | ${stats['avg_pl']:+,.2f} |"
        )
    lines.append("")


def _monthly_returns(lines: list[str], metrics: dict) -> None:
    monthly = metrics.get("monthly_returns", [])
    if not monthly:
        return

    lines.append("## Monthly Returns")
    lines.append("")
    lines.append("| Month | Return | P&L | Ending Equity |")
    lines.append("|---|---|---|---|")
    for m in monthly:
        lines.append(
            f"| {m['month']} | {m['return_pct']:+.2f}% | ${m['pl']:+,.2f} | ${m['ending_equity']:,.2f} |"
        )
    lines.append("")

    # Monthly stats
    rets = [m["return_pct"] for m in monthly]
    pos_months = sum(1 for r in rets if r > 0)
    neg_months = sum(1 for r in rets if r < 0)
    avg_month = sum(rets) / len(rets) if rets else 0
    best = max(rets) if rets else 0
    worst = min(rets) if rets else 0

    lines.append(f"- **Positive months**: {pos_months}/{len(rets)}")
    lines.append(f"- **Avg monthly return**: {avg_month:+.2f}%")
    lines.append(f"- **Best month**: {best:+.2f}%")
    lines.append(f"- **Worst month**: {worst:+.2f}%")
    lines.append("")


def _notable_trades(lines: list[str], metrics: dict) -> None:
    notable = metrics.get("notable_trades", {})
    if not notable:
        return

    lines.append("## Notable Trades")
    lines.append("")

    best = notable.get("best_trade", {})
    worst = notable.get("worst_trade", {})

    if best:
        lines.append(f"- **Best**: {best.get('symbol')} on {best.get('date')} — "
                      f"${best.get('pl', 0):+,.2f} ({best.get('return_pct', 0):+.2f}%)")
    if worst:
        lines.append(f"- **Worst**: {worst.get('symbol')} on {worst.get('date')} — "
                      f"${worst.get('pl', 0):+,.2f} ({worst.get('return_pct', 0):+.2f}%)")
    lines.append("")


def _parameters(lines: list[str], metrics: dict) -> None:
    params = metrics.get("parameters", {})
    if not params:
        return

    lines.append("## Parameters Used")
    lines.append("")
    lines.append(f"- **Momentum min**: {params.get('momentum_min', 40)}")
    lines.append(f"- **RS min**: {params.get('rs_min', 0)}")
    lines.append(f"- **ATR stop multiplier**: {params.get('atr_stop_mult', 2.0)}x")
    lines.append(f"- **Risk per trade**: {params.get('risk_pct', 1.0)}%")
    lines.append("")


def _recommendations(lines: list[str], metrics: dict) -> None:
    lines.append("## Recommendations")
    lines.append("")

    s = metrics.get("summary", {})
    t = metrics.get("trade_stats", {})
    r = metrics.get("risk_metrics", {})

    total_return = s.get("total_return_pct", 0)
    win_rate = t.get("win_rate_pct", 0)
    sharpe = r.get("sharpe_ratio", 0)
    max_dd = r.get("max_drawdown_pct", 0)
    profit_factor = t.get("profit_factor", 0)

    # Overall verdict
    if total_return > 0 and sharpe > 1.0 and profit_factor > 1.5:
        lines.append("**VERDICT: POSITIVE EDGE DETECTED** — strategy shows evidence of profitable returns.")
    elif total_return > 0 and profit_factor > 1.0:
        lines.append("**VERDICT: MARGINAL EDGE** — positive but needs parameter tuning.")
    else:
        lines.append("**VERDICT: NO EDGE DETECTED** — strategy needs significant revision.")

    lines.append("")

    # Specific recommendations
    if win_rate < 50:
        lines.append("- Consider raising momentum_min threshold to improve win rate")
    if max_dd > 15:
        lines.append("- Max drawdown exceeds 15% — reduce risk_pct or tighten stops")
    if max_dd > 20:
        lines.append("- CRITICAL: drawdown >20% would trigger circuit breaker repeatedly")
    if profit_factor < 1.0:
        lines.append("- Profit factor < 1.0 — system loses money. Do NOT trade live.")
    if profit_factor > 2.0:
        lines.append("- Strong profit factor — consider slightly increasing position sizes")
    if sharpe < 0.5:
        lines.append("- Sharpe < 0.5 — returns are not well-compensated for risk taken")
    if sharpe > 1.5:
        lines.append("- Excellent risk-adjusted returns (Sharpe > 1.5)")

    lines.append("")
