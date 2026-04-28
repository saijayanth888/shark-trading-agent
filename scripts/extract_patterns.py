"""
Pattern Extractor — computes statistical edges from KB historical bars.

Run weekly during kb-refresh. Produces:
  - kb/patterns/calendar_effects.json   (PEAD, pre-FOMC drift, day-of-week, month effects)
  - kb/patterns/sector_rotation.json    (sector momentum, leadership rankings)
  - kb/patterns/regime_outcomes.json    (per-ticker stats by regime)
  - kb/patterns/ticker_base_rates.json  (per-ticker setup win rates from kb/trades/)
  - kb/patterns/anti_patterns.json      (ticker+setup combos that historically fail)

These files are read by pre-market scoring to add a HISTORICAL EDGE bonus/penalty
on top of the live Perplexity intel.

Run directly:
    python scripts/extract_patterns.py
"""
from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Ensure repo root on path when run as a script
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from shark.data.knowledge_base import (  # noqa: E402
    load_historical_bars,
    load_closed_trades,
    _PATTERNS_DIR,
    _BARS_DIR,
    _write_json,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# Public entry point
# ===========================================================================

def extract_all_patterns() -> dict[str, Any]:
    """Run all pattern extractors and write outputs to kb/patterns/."""
    stats: dict[str, Any] = {}

    stats["calendar_effects"] = extract_calendar_effects()
    stats["sector_rotation"] = extract_sector_rotation()
    stats["regime_outcomes"] = extract_regime_outcomes()
    stats["ticker_base_rates"] = extract_ticker_base_rates()
    stats["anti_patterns"] = extract_anti_patterns()

    return stats


# ===========================================================================
# 1) Calendar Effects (day-of-week, month, pre/post-FOMC drift, PEAD)
# ===========================================================================

def extract_calendar_effects() -> int:
    """Compute calendar-effect statistics from SPY's 2-year history.

    Output keys:
      day_of_week.{0..4}   — Mon-Fri average return + win rate
      month_of_year.{1..12} — January-December averages
      pre_fomc_drift       — SPY return on day-before-FOMC
      post_earnings_drift  — needs trade history; placeholder if empty
    """
    spy = load_historical_bars("SPY")
    if spy.empty:
        logger.warning("calendar_effects: SPY bars not in KB — skipping")
        _write_json(_PATTERNS_DIR / "calendar_effects.json", {})
        return 0

    spy = spy.copy()
    spy["ret"] = spy["close"].pct_change()
    spy["dow"] = spy["timestamp"].dt.dayofweek      # Mon=0 .. Sun=6
    spy["month"] = spy["timestamp"].dt.month
    spy = spy.dropna(subset=["ret"])

    # Day-of-week
    dow_stats: dict[str, Any] = {}
    dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    for d in range(5):
        sub = spy[spy["dow"] == d]
        if len(sub) < 10:
            continue
        dow_stats[dow_names[d]] = {
            "n": int(len(sub)),
            "avg_return_pct": round(float(sub["ret"].mean()) * 100, 4),
            "win_rate": round(float((sub["ret"] > 0).mean()), 4),
            "median_return_pct": round(float(sub["ret"].median()) * 100, 4),
        }

    # Month-of-year
    month_stats: dict[str, Any] = {}
    month_names = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                   7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    for m in range(1, 13):
        sub = spy[spy["month"] == m]
        if len(sub) < 5:
            continue
        month_stats[month_names[m]] = {
            "n": int(len(sub)),
            "avg_return_pct": round(float(sub["ret"].mean()) * 100, 4),
            "win_rate": round(float((sub["ret"] > 0).mean()), 4),
        }

    # Pre/post FOMC drift — uses the static macro calendar
    fomc_stats = _compute_fomc_drift(spy)

    payload = {
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "source_ticker": "SPY",
        "lookback_bars": int(len(spy)),
        "day_of_week": dow_stats,
        "month_of_year": month_stats,
        "fomc_drift": fomc_stats,
    }
    _write_json(_PATTERNS_DIR / "calendar_effects.json", payload)
    return len(dow_stats) + len(month_stats) + len(fomc_stats)


def _compute_fomc_drift(spy: pd.DataFrame) -> dict[str, Any]:
    """For each historical FOMC date, compute SPY return on day-before & day-of."""
    try:
        from shark.data.macro_calendar import _EVENTS  # type: ignore
    except Exception as exc:
        logger.warning("Cannot import macro_calendar._EVENTS: %s", exc)
        return {}

    pre_returns: list[float] = []
    same_returns: list[float] = []
    post_returns: list[float] = []

    spy = spy.copy()
    spy["date_key"] = spy["timestamp"].dt.date

    for ev in _EVENTS:
        if (ev.get("type") or "").upper() != "FOMC":
            continue
        ev_date_str = ev.get("date") or ""
        if not ev_date_str:
            continue
        try:
            ev_date = date.fromisoformat(ev_date_str)
        except ValueError:
            continue

        # Find SPY rows around FOMC
        same_row = spy[spy["date_key"] == ev_date]
        if same_row.empty:
            continue

        idx = same_row.index[0]
        if idx > 0:
            pre_returns.append(float(spy.iloc[idx - 1]["ret"]))
        same_returns.append(float(spy.iloc[idx]["ret"]))
        if idx + 1 < len(spy):
            post_returns.append(float(spy.iloc[idx + 1]["ret"]))

    def _summary(arr: list[float]) -> dict[str, Any]:
        if not arr:
            return {"n": 0}
        a = np.array(arr)
        return {
            "n": int(len(a)),
            "avg_return_pct": round(float(a.mean()) * 100, 4),
            "win_rate": round(float((a > 0).mean()), 4),
            "median_return_pct": round(float(np.median(a)) * 100, 4),
        }

    return {
        "pre_fomc_day": _summary(pre_returns),
        "fomc_day": _summary(same_returns),
        "post_fomc_day": _summary(post_returns),
    }


# ===========================================================================
# 2) Sector Rotation (which sector ETFs are leading)
# ===========================================================================

def extract_sector_rotation() -> int:
    """Compute trailing returns for each sector ETF and rank them."""
    try:
        from shark.data.watchlist import SECTOR_ETFS
    except Exception as exc:
        logger.warning("sector_rotation: cannot import SECTOR_ETFS: %s", exc)
        _write_json(_PATTERNS_DIR / "sector_rotation.json", {})
        return 0

    rankings: dict[str, dict[str, Any]] = {}

    for sector_name, etf in SECTOR_ETFS.items():
        bars = load_historical_bars(etf)
        if bars.empty or len(bars) < 60:
            continue
        bars = bars.sort_values("timestamp").reset_index(drop=True)
        close = bars["close"]
        ret_1d = float(close.iloc[-1] / close.iloc[-2] - 1) if len(close) >= 2 else 0.0
        ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0.0
        ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0.0
        ret_60d = float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) >= 61 else 0.0

        rankings[sector_name] = {
            "etf": etf,
            "return_1d_pct": round(ret_1d * 100, 4),
            "return_5d_pct": round(ret_5d * 100, 4),
            "return_20d_pct": round(ret_20d * 100, 4),
            "return_60d_pct": round(ret_60d * 100, 4),
        }

    # Build leadership ranking by 20d return
    sorted_by_20d = sorted(
        rankings.items(),
        key=lambda kv: kv[1].get("return_20d_pct", 0),
        reverse=True,
    )
    leadership = [
        {"rank": i + 1, "sector": name, "return_20d_pct": stats["return_20d_pct"]}
        for i, (name, stats) in enumerate(sorted_by_20d)
    ]

    payload = {
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "lookback_days": 60,
        "sectors": rankings,
        "leadership_ranking": leadership,
    }
    _write_json(_PATTERNS_DIR / "sector_rotation.json", payload)
    return len(rankings)


# ===========================================================================
# 3) Regime Outcomes (per-ticker stats classified by SPY regime)
# ===========================================================================

def extract_regime_outcomes() -> int:
    """For each ticker, summarize its returns under different SPY regimes."""
    spy = load_historical_bars("SPY")
    if spy.empty:
        logger.warning("regime_outcomes: SPY missing — skipping")
        _write_json(_PATTERNS_DIR / "regime_outcomes.json", {})
        return 0

    # Classify each SPY bar into a regime: BULL_QUIET / BULL_VOLATILE / BEAR_*
    spy = spy.copy().sort_values("timestamp").reset_index(drop=True)
    spy["sma50"] = spy["close"].rolling(50).mean()
    spy["ret"] = spy["close"].pct_change()
    spy["atr"] = (spy["high"] - spy["low"]).rolling(14).mean()
    spy["atr_pct"] = spy["atr"] / spy["close"]

    if len(spy) < 60:
        _write_json(_PATTERNS_DIR / "regime_outcomes.json", {})
        return 0

    median_atr_pct = float(spy["atr_pct"].median())
    spy["bull"] = spy["close"] > spy["sma50"]
    spy["volatile"] = spy["atr_pct"] > median_atr_pct
    spy["regime"] = spy.apply(_label_regime, axis=1)
    spy["date_key"] = spy["timestamp"].dt.date

    regime_by_date = dict(zip(spy["date_key"], spy["regime"]))

    # Sample some popular tickers (not all 500 — keep file size small)
    sample_tickers = _get_sample_tickers(50)

    out: dict[str, Any] = {}
    for sym in sample_tickers:
        bars = load_historical_bars(sym)
        if bars.empty or len(bars) < 50:
            continue
        bars = bars.copy().sort_values("timestamp").reset_index(drop=True)
        bars["ret"] = bars["close"].pct_change()
        bars["date_key"] = bars["timestamp"].dt.date
        bars["regime"] = bars["date_key"].map(regime_by_date)

        ticker_stats: dict[str, Any] = {}
        for regime_name, sub in bars.groupby("regime"):
            if pd.isna(regime_name) or len(sub) < 10:
                continue
            ticker_stats[str(regime_name)] = {
                "n_days": int(len(sub)),
                "avg_daily_return_pct": round(float(sub["ret"].mean()) * 100, 4),
                "win_rate": round(float((sub["ret"] > 0).mean()), 4),
                "vol_pct": round(float(sub["ret"].std() or 0) * 100, 4),
            }
        if ticker_stats:
            out[sym] = ticker_stats

    payload = {
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "regime_definition": "BULL/BEAR via SPY > SMA50; QUIET/VOLATILE via ATR pct vs median",
        "median_atr_pct": round(median_atr_pct, 6),
        "tickers": out,
    }
    _write_json(_PATTERNS_DIR / "regime_outcomes.json", payload)
    return len(out)


def _label_regime(row: pd.Series) -> str:
    if pd.isna(row.get("sma50")) or pd.isna(row.get("atr_pct")):
        return "UNKNOWN"
    bull = bool(row["bull"])
    volatile = bool(row["volatile"])
    if bull and not volatile:
        return "BULL_QUIET"
    if bull and volatile:
        return "BULL_VOLATILE"
    if not bull and not volatile:
        return "BEAR_QUIET"
    return "BEAR_VOLATILE"


def _get_sample_tickers(n: int) -> list[str]:
    """Return the top-N most common KB tickers (by file size as a proxy for liquidity)."""
    files = [p for p in _BARS_DIR.glob("*.json") if not p.name.startswith("_")]
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return [p.stem for p in files[:n]]


# ===========================================================================
# 4) Ticker Base Rates (from closed trades — needs trade history)
# ===========================================================================

def extract_ticker_base_rates() -> int:
    """Compute per-ticker win rate from kb/trades/ records."""
    trades = load_closed_trades()
    if not trades:
        # Cold start — write empty file so loaders return None gracefully
        _write_json(_PATTERNS_DIR / "ticker_base_rates.json", {})
        return 0

    by_ticker: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for t in trades:
        ticker = (t.get("ticker") or t.get("symbol") or "").upper()
        regime = t.get("regime") or "UNKNOWN"
        pnl_pct = t.get("pnl_pct")
        if not ticker or pnl_pct is None:
            continue
        by_ticker[ticker][regime].append(float(pnl_pct))

    out: dict[str, Any] = {}
    for ticker, regimes in by_ticker.items():
        ticker_stats: dict[str, Any] = {}
        for regime, pnls in regimes.items():
            wins = sum(1 for p in pnls if p > 0)
            ticker_stats[regime] = {
                "trades": len(pnls),
                "wins": wins,
                "win_rate": round(wins / len(pnls), 4) if pnls else 0,
                "avg_pnl": round(float(np.mean(pnls)), 4) if pnls else 0,
                "expectancy": round(float(np.mean(pnls)), 4) if pnls else 0,
            }
        if ticker_stats:
            out[ticker] = ticker_stats

    payload = out
    _write_json(_PATTERNS_DIR / "ticker_base_rates.json", payload)
    return len(out)


# ===========================================================================
# 5) Anti-patterns (setups that historically fail)
# ===========================================================================

def extract_anti_patterns() -> int:
    """Identify (ticker, setup) combinations with historical loss rate >70%.

    Currently relies on closed trades; will become more powerful as trade
    history accumulates. Writes empty file on cold start.
    """
    trades = load_closed_trades()
    if not trades:
        _write_json(_PATTERNS_DIR / "anti_patterns.json", {})
        return 0

    # Group by (ticker, regime) — a simple anti-pattern definition.
    # Future: add (ticker, setup_type) grouping once we tag setups.
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t in trades:
        ticker = (t.get("ticker") or t.get("symbol") or "").upper()
        regime = t.get("regime") or "UNKNOWN"
        pnl_pct = t.get("pnl_pct")
        if not ticker or pnl_pct is None:
            continue
        grouped[(ticker, regime)].append(float(pnl_pct))

    anti: dict[str, Any] = {}
    for (ticker, regime), pnls in grouped.items():
        if len(pnls) < 3:
            continue
        loss_rate = float(np.mean([1 if p <= 0 else 0 for p in pnls]))
        if loss_rate >= 0.70:
            pid = f"{ticker}_{regime}_LOSS"
            anti[pid] = {
                "description": f"{ticker} in {regime} regime: {len(pnls)} trades, {loss_rate*100:.0f}% loss rate",
                "occurrences": len(pnls),
                "loss_rate": round(loss_rate, 4),
                "avg_pnl": round(float(np.mean(pnls)), 4),
                "applies_to": {"symbol": ticker, "regime": regime},
                "action": f"REJECT new entries on {ticker} during {regime} regime",
            }

    _write_json(_PATTERNS_DIR / "anti_patterns.json", anti)
    return len(anti)


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stats = extract_all_patterns()
    print(json.dumps(stats, indent=2))
