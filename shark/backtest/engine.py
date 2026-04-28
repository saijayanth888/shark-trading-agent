"""
shark/backtest/engine.py
-------------------------
Core backtesting simulation engine.

Walks through historical data bar-by-bar, applying the exact same entry/exit
rules as the live system. Tracks portfolio equity, open trades, and produces
a complete trade log + equity curve for metrics computation.

Designed to run inside a cloud routine (weekly-backtest phase) or locally.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from shark.backtest.data_loader import HistoricalDataLoader, get_default_symbols
from shark.backtest.strategy import (
    Trade,
    TradeStatus,
    check_entry,
    check_exits,
    compute_indicators_at,
    compute_rs_at,
    compute_shares,
    detect_regime_at,
)
from shark.backtest.metrics import compute_metrics

logger = logging.getLogger(__name__)

# Hard limits matching TRADING-STRATEGY.md
_MAX_OPEN_POSITIONS = 6
_MAX_NEW_TRADES_PER_WEEK = 3
_CASH_BUFFER_PCT = 15.0


class BacktestEngine:
    """Bar-by-bar simulation engine for strategy validation."""

    def __init__(
        self,
        starting_capital: float = 100_000.0,
        symbols: list[str] | None = None,
        lookback_days: int = 365,
        momentum_min: float = 40.0,
        rs_min: float = 0.0,
        atr_stop_mult: float = 2.0,
        risk_pct: float = 1.0,
    ):
        self.starting_capital = starting_capital
        self.symbols = symbols or get_default_symbols()
        self.lookback_days = lookback_days

        # Tunable parameters
        self.momentum_min = momentum_min
        self.rs_min = rs_min
        self.atr_stop_mult = atr_stop_mult
        self.risk_pct = risk_pct

        # State
        self.cash = starting_capital
        self.peak_equity = starting_capital
        self.open_trades: list[Trade] = []
        self.closed_trades: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.weekly_trade_count = 0
        self._current_week: str = ""

    def run(self) -> dict[str, Any]:
        """Execute the full backtest. Returns metrics dict."""
        logger.info(
            "=== BACKTEST START: capital=$%.2f symbols=%d lookback=%dd ===",
            self.starting_capital, len(self.symbols), self.lookback_days,
        )
        logger.info(
            "Parameters: momentum_min=%.0f rs_min=%.1f atr_stop=%.1fx risk=%.1f%%",
            self.momentum_min, self.rs_min, self.atr_stop_mult, self.risk_pct,
        )

        # Load data
        loader = HistoricalDataLoader(self.symbols, self.lookback_days)
        data = loader.load_all()

        spy_df = loader.get_benchmark()
        if spy_df is None or len(spy_df) < 60:
            logger.error("Insufficient SPY data for backtest")
            return {"error": "insufficient SPY data", "metrics": {}}

        available = loader.available_symbols
        logger.info("Data loaded: %d symbols available", len(available))

        # Determine simulation range (start after warmup)
        warmup = 55  # need 50 bars for SMA-50 + cushion
        sim_length = len(spy_df) - warmup

        if sim_length < 20:
            logger.error("Simulation period too short (%d bars)", sim_length)
            return {"error": "too few bars for simulation", "metrics": {}}

        logger.info("Simulating %d trading days", sim_length)

        # Bar-by-bar simulation
        for i in range(warmup, len(spy_df)):
            date_str = str(spy_df.iloc[i].get("timestamp", f"day-{i}"))[:10]
            self._process_bar(i, date_str, data, spy_df, available)

        # Close any remaining open trades at last price
        self._close_all_open(spy_df, data)

        # Compute metrics
        metrics = compute_metrics(
            self.closed_trades, self.equity_curve, self.starting_capital,
        )

        metrics["parameters"] = {
            "momentum_min": self.momentum_min,
            "rs_min": self.rs_min,
            "atr_stop_mult": self.atr_stop_mult,
            "risk_pct": self.risk_pct,
            "starting_capital": self.starting_capital,
            "symbols_tested": len(available),
            "simulation_days": sim_length,
        }

        logger.info(
            "=== BACKTEST COMPLETE: %d trades, return=%.2f%%, sharpe=%.2f, max_dd=%.2f%% ===",
            metrics["trade_stats"]["total_trades"],
            metrics["summary"]["total_return_pct"],
            metrics["risk_metrics"]["sharpe_ratio"],
            metrics["risk_metrics"]["max_drawdown_pct"],
        )

        return metrics

    # ------------------------------------------------------------------
    # Bar processing
    # ------------------------------------------------------------------

    def _process_bar(
        self,
        bar_index: int,
        date_str: str,
        data: dict[str, pd.DataFrame],
        spy_df: pd.DataFrame,
        symbols: list[str],
    ) -> None:
        """Process a single bar: check exits, then check entries."""

        # Weekly trade counter reset
        week_key = date_str[:4] + "-W" + str(pd.Timestamp(date_str).isocalendar()[1]) if len(date_str) >= 10 else ""
        if week_key != self._current_week:
            self._current_week = week_key
            self.weekly_trade_count = 0

        # Detect regime
        regime = detect_regime_at(spy_df, bar_index)

        # --- EXITS first ---
        self._process_exits(bar_index, data, regime)

        # --- ENTRIES ---
        if regime.get("new_trades_allowed", False):
            self._process_entries(bar_index, date_str, data, spy_df, symbols, regime)

        # Record equity
        equity = self._compute_equity(bar_index, data)
        self.peak_equity = max(self.peak_equity, equity)
        dd_pct = (self.peak_equity - equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0

        self.equity_curve.append({
            "date": date_str,
            "equity": round(equity, 2),
            "drawdown_pct": round(dd_pct, 2),
            "open_positions": len(self.open_trades),
            "regime": regime.get("regime", "UNKNOWN"),
        })

    def _process_exits(
        self,
        bar_index: int,
        data: dict[str, pd.DataFrame],
        regime: dict[str, Any],
    ) -> None:
        """Check exit conditions for all open trades."""
        trades_to_remove: list[Trade] = []

        for trade in self.open_trades:
            df = data.get(trade.symbol)
            if df is None or bar_index >= len(df):
                continue

            current_price = float(df.iloc[bar_index]["close"])
            trade.days_held += 1

            # Get current ATR
            indicators = compute_indicators_at(df, bar_index)
            current_atr = indicators["atr_14"] if indicators else trade.atr_at_entry

            # Check exits
            actions = check_exits(trade, current_price, current_atr, regime)

            for action in actions:
                if action["action"] == "close_all":
                    pl = (current_price - trade.entry_price) * trade.remaining_shares
                    trade.realized_pl += pl
                    trade.remaining_shares = 0
                    trade.exit_price = current_price
                    trade.exit_date = str(df.iloc[bar_index].get("timestamp", ""))[:10]

                    status_map = {
                        "hard_stop": TradeStatus.CLOSED_HARD_STOP,
                        "stop_hit": TradeStatus.CLOSED_STOP,
                        "regime_shift": TradeStatus.CLOSED_REGIME_SHIFT,
                        "vol_expansion": TradeStatus.CLOSED_VOL_EXPANSION,
                        "time_decay": TradeStatus.CLOSED_TIME_DECAY,
                    }
                    trade.status = status_map.get(action["reason"], TradeStatus.CLOSED_STOP)
                    self.cash += trade.exit_price * action["shares"] + pl
                    trades_to_remove.append(trade)

                    self._log_close(trade, action)
                    break

                elif action["action"] == "partial_sell":
                    sell_shares = action["shares"]
                    pl = (current_price - trade.entry_price) * sell_shares
                    trade.realized_pl += pl
                    trade.remaining_shares -= sell_shares
                    self.cash += current_price * sell_shares
                    trade.partial_exits.append({
                        "tier": action.get("tier"),
                        "shares": sell_shares,
                        "price": current_price,
                        "pl": round(pl, 2),
                    })

            # Close if all shares sold via partials
            if trade.remaining_shares <= 0 and trade not in trades_to_remove:
                trade.status = TradeStatus.CLOSED_PARTIAL_COMPLETE
                trade.exit_price = float(df.iloc[bar_index]["close"])
                trade.exit_date = str(df.iloc[bar_index].get("timestamp", ""))[:10]
                trades_to_remove.append(trade)

        for trade in trades_to_remove:
            self.open_trades.remove(trade)
            self.closed_trades.append(self._trade_to_dict(trade))

    def _process_entries(
        self,
        bar_index: int,
        date_str: str,
        data: dict[str, pd.DataFrame],
        spy_df: pd.DataFrame,
        symbols: list[str],
        regime: dict[str, Any],
    ) -> None:
        """Scan symbols for entry signals."""
        # Guardrails
        if len(self.open_trades) >= _MAX_OPEN_POSITIONS:
            return
        if self.weekly_trade_count >= _MAX_NEW_TRADES_PER_WEEK:
            return

        # Cash buffer check
        equity = self._compute_equity(bar_index, data)
        min_cash = equity * (_CASH_BUFFER_PCT / 100)
        if self.cash <= min_cash:
            return

        open_symbols = {t.symbol for t in self.open_trades}

        for symbol in symbols:
            if symbol == "SPY" or symbol in open_symbols:
                continue
            if len(self.open_trades) >= _MAX_OPEN_POSITIONS:
                break
            if self.weekly_trade_count >= _MAX_NEW_TRADES_PER_WEEK:
                break

            df = data.get(symbol)
            if df is None or bar_index >= len(df):
                continue

            # Compute indicators
            indicators = compute_indicators_at(df, bar_index)
            if indicators is None:
                continue

            # Compute RS
            rs_composite = compute_rs_at(df, spy_df, bar_index)

            # Point-in-time PEAD detection — only sees bars up to bar_index
            pead_active = False
            try:
                from shark.data.pead import find_active_pead_setup_in_df
                pead_setup = find_active_pead_setup_in_df(df, bar_index, symbol)
                pead_active = pead_setup is not None
            except Exception:
                pead_setup = None

            # Check entry criteria (PEAD active relaxes momentum threshold)
            entry = check_entry(
                indicators, regime, rs_composite,
                momentum_min=self.momentum_min,
                rs_min=self.rs_min,
                pead_active=pead_active,
            )

            if not entry["passed"]:
                continue

            # Position sizing
            sizing = compute_shares(
                portfolio_value=equity,
                current_price=indicators["current_price"],
                atr=indicators["atr_14"],
                regime_mult=regime.get("size_mult", 1.0),
                risk_pct=self.risk_pct,
                atr_stop_mult=self.atr_stop_mult,
            )

            if sizing["shares"] <= 0:
                continue

            # Check we can afford it
            cost = sizing["shares"] * indicators["current_price"]
            if cost > self.cash - min_cash:
                continue

            # Execute entry
            trade = Trade(
                symbol=symbol,
                entry_date=date_str,
                entry_price=indicators["current_price"],
                shares=sizing["shares"],
                stop_price=sizing["stop_price"],
                atr_at_entry=indicators["atr_14"],
                regime_at_entry=regime.get("regime", "UNKNOWN"),
                momentum_score=indicators["momentum_score"],
                rs_composite=rs_composite,
                setup_tag="pead" if pead_active else "momentum",
            )

            self.open_trades.append(trade)
            self.cash -= cost
            self.weekly_trade_count += 1

            logger.debug(
                "ENTRY: %s %d shares @ $%.2f | stop=$%.2f | momentum=%.0f rs=%.2f | %s",
                symbol, sizing["shares"], indicators["current_price"],
                sizing["stop_price"], indicators["momentum_score"],
                rs_composite, regime["regime"],
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _compute_equity(self, bar_index: int, data: dict[str, pd.DataFrame]) -> float:
        """Compute total portfolio equity at current bar."""
        positions_value = 0.0
        for trade in self.open_trades:
            df = data.get(trade.symbol)
            if df is not None and bar_index < len(df):
                current_price = float(df.iloc[bar_index]["close"])
                positions_value += current_price * trade.remaining_shares
        return self.cash + positions_value

    def _close_all_open(self, spy_df: pd.DataFrame, data: dict[str, pd.DataFrame]) -> None:
        """Close any remaining open trades at end of simulation."""
        last_index = len(spy_df) - 1
        for trade in list(self.open_trades):
            df = data.get(trade.symbol)
            if df is None:
                continue
            idx = min(last_index, len(df) - 1)
            price = float(df.iloc[idx]["close"])
            pl = (price - trade.entry_price) * trade.remaining_shares
            trade.realized_pl += pl
            trade.exit_price = price
            trade.exit_date = str(df.iloc[idx].get("timestamp", ""))[:10]
            trade.status = TradeStatus.CLOSED_TARGET
            trade.remaining_shares = 0
            self.cash += price * trade.shares  # approximate
            self.closed_trades.append(self._trade_to_dict(trade))

        self.open_trades.clear()

    def _trade_to_dict(self, trade: Trade) -> dict[str, Any]:
        return {
            "symbol": trade.symbol,
            "entry_date": trade.entry_date,
            "exit_date": trade.exit_date,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "shares": trade.shares,
            "remaining_shares": trade.remaining_shares,
            "realized_pl": round(trade.realized_pl, 2),
            "return_pct": round(
                (trade.exit_price - trade.entry_price) / trade.entry_price * 100, 2
            ) if trade.entry_price > 0 else 0,
            "status": trade.status.value,
            "exit_reason": trade.status.value.lower().replace("closed_", ""),
            "regime_at_entry": trade.regime_at_entry,
            "momentum_score": trade.momentum_score,
            "rs_composite": trade.rs_composite,
            "setup_tag": trade.setup_tag,
            "days_held": trade.days_held,
            "partial_exits": trade.partial_exits,
            "atr_at_entry": trade.atr_at_entry,
            "stop_price": trade.stop_price,
        }

    def _log_close(self, trade: Trade, action: dict) -> None:
        pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price * 100
        logger.debug(
            "EXIT: %s %s | P&L=$%.2f (%.1f%%) | held %dd | %s",
            trade.symbol, action["reason"],
            trade.realized_pl, pnl_pct,
            trade.days_held, action.get("detail", ""),
        )


def run_backtest(
    starting_capital: float = 100_000.0,
    symbols: list[str] | None = None,
    lookback_days: int = 365,
    momentum_min: float = 40.0,
    rs_min: float = 0.0,
    atr_stop_mult: float = 2.0,
    risk_pct: float = 1.0,
) -> dict[str, Any]:
    """Convenience function to run a backtest with given parameters."""
    engine = BacktestEngine(
        starting_capital=starting_capital,
        symbols=symbols,
        lookback_days=lookback_days,
        momentum_min=momentum_min,
        rs_min=rs_min,
        atr_stop_mult=atr_stop_mult,
        risk_pct=risk_pct,
    )
    return engine.run()
