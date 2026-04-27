"""
Guardrails — Python-level hard stops for trade execution.

No AI. No exceptions. These rules are enforced unconditionally before any
order reaches Alpaca. Instantiate once and call run_all() before every trade.
"""

import os
import logging
from typing import Any

from shark.data.macro_calendar import check_macro_calendar

logger = logging.getLogger(__name__)


class Guardrails:
    """
    Hard-coded trading limits that protect the account from runaway losses,
    over-concentration, and excessive activity.

    All limits are read from environment variables at construction time so
    they can be tuned without code changes.
    """

    def __init__(self) -> None:
        self.max_positions: int = int(os.getenv("MAX_POSITIONS", "6"))
        self.max_position_pct: float = float(os.getenv("MAX_POSITION_PCT", "0.20"))
        self.max_weekly_trades: int = int(os.getenv("MAX_WEEKLY_TRADES", "3"))
        self.min_cash_buffer: float = float(os.getenv("MIN_CASH_BUFFER_PCT", "0.15"))
        self.circuit_breaker_pct: float = float(
            os.getenv("CIRCUIT_BREAKER_PCT", "0.15")
        )
        self.max_sector_failures: int = int(
            os.getenv("MAX_SECTOR_FAILURES", "2")
        )
        self.max_sector_concentration: int = int(
            os.getenv("MAX_SECTOR_CONCENTRATION", "3")
        )
        self.min_momentum_score: float = float(
            os.getenv("MIN_MOMENTUM_SCORE", "40.0")
        )

    # ------------------------------------------------------------------
    # Individual checks — each returns (passed: bool, message: str)
    # ------------------------------------------------------------------

    def check_max_positions(self, current_count: int) -> tuple[bool, str]:
        """
        Ensure opening a new position won't exceed the maximum allowed.

        Args:
            current_count: Number of positions currently open.

        Returns:
            (True, ok_msg) if current_count + 1 <= max_positions, else (False, fail_msg).
        """
        new_count = current_count + 1
        if new_count <= self.max_positions:
            return True, (
                f"OK — {new_count}/{self.max_positions} positions after trade."
            )
        return False, (
            f"FAIL — adding position would reach {new_count}, "
            f"limit is {self.max_positions}."
        )

    def check_position_size(
        self, trade_value: float, portfolio_value: float
    ) -> tuple[bool, str]:
        """
        Ensure a single position does not exceed the max position-size percentage.

        Args:
            trade_value: Total dollar cost of the proposed trade.
            portfolio_value: Current total portfolio value.

        Returns:
            (True, ok_msg) if within limits, else (False, fail_msg).
        """
        if portfolio_value <= 0:
            return False, "FAIL — portfolio_value is zero or negative."

        pct = trade_value / portfolio_value
        if pct <= self.max_position_pct:
            return True, (
                f"OK — position is {pct:.1%} of portfolio "
                f"(limit {self.max_position_pct:.0%})."
            )
        return False, (
            f"FAIL — position is {pct:.1%} of portfolio, "
            f"exceeds limit of {self.max_position_pct:.0%}."
        )

    def check_weekly_trade_count(self, trades_this_week: int) -> tuple[bool, str]:
        """
        Ensure placing another trade won't exceed the weekly trade limit.

        Args:
            trades_this_week: Trades already placed this calendar week.

        Returns:
            (True, ok_msg) or (False, fail_msg).
        """
        new_count = trades_this_week + 1
        if new_count <= self.max_weekly_trades:
            return True, (
                f"OK — {new_count}/{self.max_weekly_trades} trades this week."
            )
        return False, (
            f"FAIL — would be trade #{new_count} this week, "
            f"limit is {self.max_weekly_trades}."
        )

    def check_cash_buffer(
        self, cash_after_trade: float, portfolio_value: float
    ) -> tuple[bool, str]:
        """
        Ensure a minimum cash buffer remains after the trade executes.

        Args:
            cash_after_trade: Projected cash balance after the trade cost.
            portfolio_value: Current total portfolio value.

        Returns:
            (True, ok_msg) or (False, fail_msg).
        """
        if portfolio_value <= 0:
            return False, "FAIL — portfolio_value is zero or negative."

        buffer_pct = cash_after_trade / portfolio_value
        if buffer_pct >= self.min_cash_buffer:
            return True, (
                f"OK — cash buffer after trade: {buffer_pct:.1%} "
                f"(min {self.min_cash_buffer:.0%})."
            )
        return False, (
            f"FAIL — cash buffer after trade would be {buffer_pct:.1%}, "
            f"below minimum {self.min_cash_buffer:.0%}."
        )

    def check_circuit_breaker(
        self, current_equity: float, peak_equity: float
    ) -> tuple[bool, str]:
        """
        Halt all trading if drawdown from peak equity exceeds the circuit-breaker threshold.

        Args:
            current_equity: Current total portfolio value.
            peak_equity: Highest portfolio value recorded (historical peak).

        Returns:
            (True, ok_msg) if within drawdown limits, else (False, fail_msg).
        """
        if peak_equity <= 0:
            return False, "FAIL — peak_equity is zero or negative."

        drawdown = (peak_equity - current_equity) / peak_equity
        if drawdown < self.circuit_breaker_pct:
            return True, (
                f"OK — drawdown from peak is {drawdown:.1%} "
                f"(limit {self.circuit_breaker_pct:.0%})."
            )
        return False, (
            f"FAIL — circuit breaker triggered! Drawdown {drawdown:.1%} "
            f"exceeds {self.circuit_breaker_pct:.0%} threshold."
        )

    def check_sector_failures(
        self, sector: str, recent_trades: list[dict[str, Any]]
    ) -> tuple[bool, str]:
        """
        Block trading in a sector that has experienced consecutive recent losses.

        A "failure" is any trade dict where result == "loss". Consecutive means
        there is no winning trade between the failures.

        Args:
            sector: Sector of the proposed trade (e.g. "Technology").
            recent_trades: List of recent trade dicts with keys: sector, result
                ("win" | "loss"). Ordered most recent first.

        Returns:
            (True, ok_msg) if under the consecutive failure limit, else (False, fail_msg).
        """
        consecutive_failures = 0
        for trade in recent_trades:
            if trade.get("sector") != sector:
                continue
            if trade.get("result") == "loss":
                consecutive_failures += 1
            else:
                # A win breaks the consecutive streak
                break

        if consecutive_failures < self.max_sector_failures:
            return True, (
                f"OK — {consecutive_failures} consecutive {sector} sector losses "
                f"(limit {self.max_sector_failures})."
            )
        return False, (
            f"FAIL — {consecutive_failures} consecutive losses in {sector} sector; "
            f"limit is {self.max_sector_failures}."
        )

    def check_sector_concentration(
        self, sector: str, positions: list[dict[str, Any]]
    ) -> tuple[bool, str]:
        """
        Prevent over-concentration in a single sector.

        Even if individual position sizes are ok, having 4+ positions in
        the same sector creates correlated risk that can blow up the portfolio
        on a sector-wide downturn.

        Args:
            sector: Sector of the proposed trade.
            positions: Current open positions (each must have 'sector' key
                       or we fall back to symbol-based lookup).

        Returns:
            (True, ok_msg) or (False, fail_msg).
        """
        same_sector_count = sum(
            1 for p in positions
            if p.get("sector", "").lower() == sector.lower()
        )

        new_count = same_sector_count + 1
        if new_count <= self.max_sector_concentration:
            return True, (
                f"OK — {new_count}/{self.max_sector_concentration} positions "
                f"in {sector} sector after trade."
            )
        return False, (
            f"FAIL — would be {new_count} positions in {sector} sector, "
            f"max concentration is {self.max_sector_concentration}."
        )

    def check_regime_gate(
        self, regime: str,
    ) -> tuple[bool, str]:
        """
        Block new trades in BEAR market regimes.

        BULL_QUIET / BULL_VOLATILE: allowed
        BEAR_QUIET / BEAR_VOLATILE: blocked
        UNKNOWN: allowed with caution

        Args:
            regime: Current market regime string.

        Returns:
            (True, ok_msg) or (False, fail_msg).
        """
        if "BEAR" in regime.upper():
            return False, (
                f"FAIL — market regime is {regime}. "
                f"No new longs allowed in BEAR regimes."
            )
        return True, f"OK — market regime {regime} allows new trades."

    def check_macro_events(self) -> tuple[bool, str]:
        """
        Block trades on major macro event days (FOMC, CPI, NFP).

        Returns:
            (True, ok_msg) or (False, fail_msg).
        """
        macro = check_macro_calendar()
        impact = macro.get("impact_level", "NORMAL")

        if impact in ("CRITICAL", "HIGH"):
            desc = macro.get("description", "major event")
            return False, f"FAIL — macro block: {impact} — {desc}"

        if impact == "ELEVATED":
            desc = macro.get("description", "nearby event")
            return True, f"CAUTION — {desc} (half-size recommended)"

        return True, "OK — no major macro events nearby."

    def check_momentum_score(
        self, momentum_score: float,
    ) -> tuple[bool, str]:
        """
        Block trades with weak technical momentum.

        Args:
            momentum_score: Composite momentum score (0-100) from technical.py

        Returns:
            (True, ok_msg) or (False, fail_msg).
        """
        if momentum_score >= self.min_momentum_score:
            return True, (
                f"OK — momentum score {momentum_score:.0f}/100 "
                f"(min {self.min_momentum_score:.0f})."
            )
        return False, (
            f"FAIL — momentum score {momentum_score:.0f}/100 "
            f"below minimum {self.min_momentum_score:.0f}."
        )

    # ------------------------------------------------------------------
    # Aggregate runner
    # ------------------------------------------------------------------

    def run_all(
        self,
        proposed_trade: dict[str, Any],
        account: dict[str, Any],
        weekly_count: int,
        peak_equity: float,
        recent_trades: list[dict[str, Any]],
        regime: str = "BULL_QUIET",
        momentum_score: float = 100.0,
    ) -> dict[str, Any]:
        """
        Run every guardrail check and return a consolidated result.

        Args:
            proposed_trade: Dict with at minimum: symbol, qty, estimated_cost, sector.
            account: Dict with portfolio_value (float) and cash (float).
            weekly_count: Number of trades placed this week already.
            peak_equity: Historical peak portfolio value.
            recent_trades: List of recent trade dicts for sector-failure check.
                Each dict has: sector (str), result ("win" | "loss").
            regime: Current market regime string (from market_regime.py).
            momentum_score: Technical momentum score (0-100) from technical.py.

        Returns:
            Dict compatible with risk_manager.check_risk() output:
                approved (bool), violations (list[str]),
                adjusted_size (int), checks (dict),
                macro_multiplier (float).
        """
        portfolio_value = float(account.get("portfolio_value", 0))
        cash = float(account.get("cash", 0))
        estimated_cost = float(proposed_trade.get("estimated_cost", 0))
        qty = int(proposed_trade.get("qty", 0))
        sector = proposed_trade.get("sector", "Unknown")
        positions = account.get("positions", [])
        current_count = len(positions)

        cash_after = cash - estimated_cost
        macro_multiplier = 1.0

        checks: dict[str, dict[str, Any]] = {}

        passed, msg = self.check_max_positions(current_count)
        checks["max_positions"] = {"passed": passed, "message": msg}

        passed, msg = self.check_position_size(estimated_cost, portfolio_value)
        checks["position_size"] = {"passed": passed, "message": msg}

        passed, msg = self.check_weekly_trade_count(weekly_count)
        checks["weekly_trades"] = {"passed": passed, "message": msg}

        passed, msg = self.check_cash_buffer(cash_after, portfolio_value)
        checks["cash_buffer"] = {"passed": passed, "message": msg}

        passed, msg = self.check_circuit_breaker(portfolio_value, peak_equity)
        checks["circuit_breaker"] = {"passed": passed, "message": msg}

        passed, msg = self.check_sector_failures(sector, recent_trades)
        checks["sector_failures"] = {"passed": passed, "message": msg}

        # --- ADVANCED CHECKS ---

        passed, msg = self.check_sector_concentration(sector, positions)
        checks["sector_concentration"] = {"passed": passed, "message": msg}

        passed, msg = self.check_regime_gate(regime)
        checks["regime_gate"] = {"passed": passed, "message": msg}

        passed, msg = self.check_macro_events()
        checks["macro_events"] = {"passed": passed, "message": msg}
        if "CAUTION" in msg:
            macro_multiplier = 0.5

        passed, msg = self.check_momentum_score(momentum_score)
        checks["momentum_score"] = {"passed": passed, "message": msg}

        violations = [
            result["message"]
            for result in checks.values()
            if not result["passed"]
        ]

        approved = len(violations) == 0

        # Compute adjusted size if position-size check failed
        if not checks["position_size"]["passed"] and portfolio_value > 0 and qty > 0:
            price_per_share = estimated_cost / qty
            max_affordable = portfolio_value * self.max_position_pct
            adjusted_qty = max(1, int(max_affordable / price_per_share))
        else:
            adjusted_qty = qty

        if approved:
            logger.info(
                "Guardrails APPROVED — %s (qty=%d, cost=%.2f, regime=%s, macro=%.1f)",
                proposed_trade.get("symbol"),
                qty,
                estimated_cost,
                regime,
                macro_multiplier,
            )
        else:
            logger.warning(
                "Guardrails REJECTED — %s | violations: %s",
                proposed_trade.get("symbol"),
                violations,
            )

        return {
            "approved": approved,
            "violations": violations,
            "adjusted_size": adjusted_qty,
            "checks": checks,
            "macro_multiplier": macro_multiplier,
        }
