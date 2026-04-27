"""
shark/phases/backtest.py
--------------------------
Cloud routine phase: runs weekly backtest to validate strategy parameters.

Scheduled to run after weekly-review. Pulls historical data from Alpaca,
simulates all trading rules against it, and writes BACKTEST-REPORT.md
with metrics, regime analysis, and parameter recommendations.

No real money is involved — pure simulation using historical bars.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from shark.backtest.data_loader import get_default_symbols
from shark.backtest.engine import run_backtest
from shark.backtest.report import generate_report
from shark.memory.state import commit_memory

logger = logging.getLogger(__name__)


def run(dry_run: bool = False) -> bool:
    """Execute the weekly backtest phase.

    Steps:
        1. Load parameters from env (or defaults)
        2. Run backtest against last N days of market data
        3. Generate BACKTEST-REPORT.md
        4. Commit to git so weekly-review can read it

    Returns True on success.
    """
    logger.info("=== BACKTEST PHASE START ===")

    # Parameters — can be overridden via env vars
    starting_capital = float(os.environ.get("BACKTEST_CAPITAL", "100000"))
    lookback_days = int(os.environ.get("BACKTEST_LOOKBACK_DAYS", "365"))
    momentum_min = float(os.environ.get("BACKTEST_MOMENTUM_MIN", "40"))
    rs_min = float(os.environ.get("BACKTEST_RS_MIN", "0"))
    atr_stop_mult = float(os.environ.get("BACKTEST_ATR_STOP_MULT", "2.0"))
    risk_pct = float(os.environ.get("BACKTEST_RISK_PCT", "1.0"))

    # Custom symbols or defaults
    symbols_env = os.environ.get("BACKTEST_SYMBOLS", "")
    symbols = [s.strip().upper() for s in symbols_env.split(",") if s.strip()] if symbols_env else get_default_symbols()

    logger.info(
        "Config: capital=$%.0f lookback=%dd symbols=%d momentum>=%.0f rs>=%.1f atr_stop=%.1fx risk=%.1f%%",
        starting_capital, lookback_days, len(symbols),
        momentum_min, rs_min, atr_stop_mult, risk_pct,
    )

    if dry_run:
        logger.info("DRY RUN — skipping actual backtest execution")
        return True

    try:
        # Run the backtest
        metrics = run_backtest(
            starting_capital=starting_capital,
            symbols=symbols,
            lookback_days=lookback_days,
            momentum_min=momentum_min,
            rs_min=rs_min,
            atr_stop_mult=atr_stop_mult,
            risk_pct=risk_pct,
        )

        if "error" in metrics:
            logger.error("Backtest failed: %s", metrics["error"])
            return False

        # Generate report
        report_path = generate_report(metrics)
        logger.info("Report written: %s", report_path)

        # Log key results
        summary = metrics.get("summary", {})
        trade_stats = metrics.get("trade_stats", {})
        risk = metrics.get("risk_metrics", {})

        logger.info(
            "RESULTS: return=%.2f%% | trades=%d win_rate=%.1f%% | "
            "sharpe=%.2f max_dd=%.2f%% | profit_factor=%.2f",
            summary.get("total_return_pct", 0),
            trade_stats.get("total_trades", 0),
            trade_stats.get("win_rate_pct", 0),
            risk.get("sharpe_ratio", 0),
            risk.get("max_drawdown_pct", 0),
            trade_stats.get("profit_factor", 0),
        )

        # Commit report to git
        try:
            commit_memory("backtest: weekly strategy validation report")
            logger.info("Backtest report committed to git")
        except Exception as exc:
            logger.warning("Git commit failed (non-fatal): %s", exc)

        logger.info("=== BACKTEST PHASE COMPLETE ===")
        return True

    except Exception as exc:
        logger.error("Backtest phase failed: %s", exc, exc_info=True)
        return False
