"""
shark/backtest/metrics.py
--------------------------
Compute performance metrics from a list of completed trades and an equity curve.

Metrics:
  - Total return, CAGR
  - Win rate, profit factor
  - Average winner / loser
  - Max drawdown (peak-to-trough)
  - Sharpe ratio (annualized, 252 trading days)
  - Sortino ratio
  - Monthly returns breakdown
  - Regime-level P&L attribution
  - Exit-reason breakdown
  - Parameter sensitivity data
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def compute_metrics(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    starting_capital: float,
) -> dict[str, Any]:
    """
    Compute full performance metrics from backtest results.

    Args:
        trades: List of completed trade dicts with keys:
            symbol, entry_date, exit_date, entry_price, exit_price,
            shares, realized_pl, status, regime_at_entry, days_held
        equity_curve: List of dicts with keys: date, equity, drawdown_pct
        starting_capital: Initial portfolio value

    Returns:
        Dict with all performance metrics
    """
    if not trades:
        return _empty_metrics(starting_capital)

    # Basic trade stats
    winners = [t for t in trades if t.get("realized_pl", 0) > 0]
    losers = [t for t in trades if t.get("realized_pl", 0) < 0]
    breakeven = [t for t in trades if t.get("realized_pl", 0) == 0]

    total_trades = len(trades)
    win_count = len(winners)
    loss_count = len(losers)
    win_rate = win_count / total_trades if total_trades > 0 else 0

    total_pl = sum(t.get("realized_pl", 0) for t in trades)
    gross_profit = sum(t.get("realized_pl", 0) for t in winners)
    gross_loss = abs(sum(t.get("realized_pl", 0) for t in losers))

    avg_winner = gross_profit / win_count if win_count > 0 else 0
    avg_loser = gross_loss / loss_count if loss_count > 0 else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win_pct = (
        sum(_trade_return_pct(t) for t in winners) / win_count if win_count > 0 else 0
    )
    avg_loss_pct = (
        sum(abs(_trade_return_pct(t)) for t in losers) / loss_count if loss_count > 0 else 0
    )

    # Avg holding period
    avg_days_held = sum(t.get("days_held", 0) for t in trades) / total_trades
    avg_days_winners = (
        sum(t.get("days_held", 0) for t in winners) / win_count if win_count > 0 else 0
    )
    avg_days_losers = (
        sum(t.get("days_held", 0) for t in losers) / loss_count if loss_count > 0 else 0
    )

    # Largest trades
    best_trade = max(trades, key=lambda t: t.get("realized_pl", 0))
    worst_trade = min(trades, key=lambda t: t.get("realized_pl", 0))

    # Equity curve analysis
    ending_capital = equity_curve[-1]["equity"] if equity_curve else starting_capital
    total_return_pct = (ending_capital - starting_capital) / starting_capital * 100

    # Max drawdown
    max_dd = _max_drawdown(equity_curve)

    # Sharpe & Sortino (annualized)
    daily_returns = _daily_returns(equity_curve)
    sharpe = _sharpe_ratio(daily_returns)
    sortino = _sortino_ratio(daily_returns)

    # CAGR
    trading_days = len(equity_curve)
    years = trading_days / 252 if trading_days > 0 else 1
    if ending_capital > 0 and starting_capital > 0 and years > 0:
        cagr = (pow(ending_capital / starting_capital, 1 / years) - 1) * 100
    else:
        cagr = 0.0

    # Monthly returns
    monthly = _monthly_returns(equity_curve)

    # Regime breakdown
    regime_stats = _regime_breakdown(trades)

    # Exit reason breakdown
    exit_stats = _exit_breakdown(trades)

    # Consecutive wins/losses
    max_consec_wins, max_consec_losses = _consecutive_streaks(trades)

    return {
        "summary": {
            "starting_capital": starting_capital,
            "ending_capital": round(ending_capital, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_pl": round(total_pl, 2),
            "cagr_pct": round(cagr, 2),
        },
        "trade_stats": {
            "total_trades": total_trades,
            "winners": win_count,
            "losers": loss_count,
            "breakeven": len(breakeven),
            "win_rate_pct": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_winner": round(avg_winner, 2),
            "avg_loser": round(avg_loser, 2),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "win_loss_ratio": round(avg_winner / avg_loser, 2) if avg_loser > 0 else 0,
            "expectancy": round(total_pl / total_trades, 2),
        },
        "risk_metrics": {
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
        },
        "holding_period": {
            "avg_days": round(avg_days_held, 1),
            "avg_days_winners": round(avg_days_winners, 1),
            "avg_days_losers": round(avg_days_losers, 1),
        },
        "notable_trades": {
            "best_trade": {
                "symbol": best_trade.get("symbol"),
                "pl": round(best_trade.get("realized_pl", 0), 2),
                "return_pct": round(_trade_return_pct(best_trade), 2),
                "date": best_trade.get("entry_date"),
            },
            "worst_trade": {
                "symbol": worst_trade.get("symbol"),
                "pl": round(worst_trade.get("realized_pl", 0), 2),
                "return_pct": round(_trade_return_pct(worst_trade), 2),
                "date": worst_trade.get("entry_date"),
            },
        },
        "regime_breakdown": regime_stats,
        "exit_breakdown": exit_stats,
        "monthly_returns": monthly,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade_return_pct(trade: dict) -> float:
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("exit_price", 0)
    if entry <= 0:
        return 0.0
    return (exit_p - entry) / entry * 100


def _max_drawdown(equity_curve: list[dict]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]["equity"]
    max_dd = 0.0
    for point in equity_curve:
        eq = point["equity"]
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return max_dd


def _daily_returns(equity_curve: list[dict]) -> list[float]:
    if len(equity_curve) < 2:
        return []
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]["equity"]
        curr = equity_curve[i]["equity"]
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


def _sharpe_ratio(daily_returns: list[float], risk_free_annual: float = 0.05) -> float:
    if len(daily_returns) < 30:
        return 0.0
    rf_daily = risk_free_annual / 252
    excess = [r - rf_daily for r in daily_returns]
    mean_excess = sum(excess) / len(excess)
    variance = sum((r - mean_excess) ** 2 for r in excess) / len(excess)
    std = math.sqrt(variance) if variance > 0 else 1e-10
    return (mean_excess / std) * math.sqrt(252)


def _sortino_ratio(daily_returns: list[float], risk_free_annual: float = 0.05) -> float:
    if len(daily_returns) < 30:
        return 0.0
    rf_daily = risk_free_annual / 252
    excess = [r - rf_daily for r in daily_returns]
    mean_excess = sum(excess) / len(excess)
    downside = [r ** 2 for r in excess if r < 0]
    downside_dev = math.sqrt(sum(downside) / len(downside)) if downside else 1e-10
    return (mean_excess / downside_dev) * math.sqrt(252)


def _monthly_returns(equity_curve: list[dict]) -> list[dict]:
    if not equity_curve:
        return []

    months: dict[str, dict] = {}
    for point in equity_curve:
        date_str = str(point.get("date", ""))[:7]  # YYYY-MM
        if date_str not in months:
            months[date_str] = {"start": point["equity"], "end": point["equity"]}
        months[date_str]["end"] = point["equity"]

    result = []
    for month, vals in sorted(months.items()):
        start = vals["start"]
        end = vals["end"]
        ret_pct = (end - start) / start * 100 if start > 0 else 0
        pl = end - start
        result.append({
            "month": month,
            "return_pct": round(ret_pct, 2),
            "pl": round(pl, 2),
            "ending_equity": round(end, 2),
        })

    return result


def _regime_breakdown(trades: list[dict]) -> dict[str, dict]:
    regimes: dict[str, list] = {}
    for t in trades:
        r = t.get("regime_at_entry", "UNKNOWN")
        regimes.setdefault(r, []).append(t)

    result = {}
    for regime, regime_trades in regimes.items():
        pl = sum(t.get("realized_pl", 0) for t in regime_trades)
        wins = sum(1 for t in regime_trades if t.get("realized_pl", 0) > 0)
        result[regime] = {
            "trades": len(regime_trades),
            "total_pl": round(pl, 2),
            "win_rate_pct": round(wins / len(regime_trades) * 100, 1) if regime_trades else 0,
        }
    return result


def _exit_breakdown(trades: list[dict]) -> dict[str, dict]:
    exits: dict[str, list] = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        exits.setdefault(reason, []).append(t)

    result = {}
    for reason, reason_trades in exits.items():
        pl = sum(t.get("realized_pl", 0) for t in reason_trades)
        result[reason] = {
            "count": len(reason_trades),
            "total_pl": round(pl, 2),
            "avg_pl": round(pl / len(reason_trades), 2) if reason_trades else 0,
        }
    return result


def _consecutive_streaks(trades: list[dict]) -> tuple[int, int]:
    max_wins = max_losses = 0
    current_wins = current_losses = 0

    for t in trades:
        if t.get("realized_pl", 0) > 0:
            current_wins += 1
            current_losses = 0
        elif t.get("realized_pl", 0) < 0:
            current_losses += 1
            current_wins = 0
        else:
            current_wins = 0
            current_losses = 0

        max_wins = max(max_wins, current_wins)
        max_losses = max(max_losses, current_losses)

    return max_wins, max_losses


def _empty_metrics(starting_capital: float) -> dict[str, Any]:
    return {
        "summary": {
            "starting_capital": starting_capital,
            "ending_capital": starting_capital,
            "total_return_pct": 0.0,
            "total_pl": 0.0,
            "cagr_pct": 0.0,
        },
        "trade_stats": {
            "total_trades": 0,
            "winners": 0,
            "losers": 0,
            "breakeven": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "avg_winner": 0.0,
            "avg_loser": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "win_loss_ratio": 0.0,
            "expectancy": 0.0,
        },
        "risk_metrics": {
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
        },
        "holding_period": {"avg_days": 0, "avg_days_winners": 0, "avg_days_losers": 0},
        "notable_trades": {},
        "regime_breakdown": {},
        "exit_breakdown": {},
        "monthly_returns": [],
    }
