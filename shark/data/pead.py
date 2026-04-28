"""
PEAD — Post-Earnings Announcement Drift detector.

Bernard & Thomas (1989); Chan-Jegadeesh-Lakonishok (1996); Sadka (2006).
One of the most-replicated anomalies in finance — stocks that gap on earnings
continue to drift in the direction of the gap for ~60 trading days.

Approach (no external earnings API required):
  Detect earnings-like gap days from price+volume data. A day is flagged when
    abs(gap_pct) >= GAP_THRESHOLD AND volume >= VOLUME_MULTIPLE * avg_20d_vol
  Then compute a score bonus that DECAYS linearly across the drift window.

Public API:
    find_active_pead_setup(symbol, today=None) -> PEADSetup | None
    compute_pead_score_bonus(setup) -> int

Cold-start safe: returns None / 0 when no bars in KB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ===========================================================================
# Tuning constants — calibrate as more closed PEAD trades accumulate.
# ===========================================================================

GAP_THRESHOLD_PCT = 4.0          # |open - prev_close| / prev_close >= 4% to flag
VOLUME_MULTIPLE = 2.0            # gap-day volume >= 2x its 20-day average
LOOKBACK_DAYS = 90               # only scan the last 90 trading days for setups
DRIFT_WINDOW_DAYS = 60           # academic-standard PEAD window (≈ next quarter)
MIN_VOLUME_20D = 500_000         # ignore microcaps — need decent liquidity

# Score bonus tuning
PEAD_BONUS_AT_DAY_1 = 6          # full bonus right after the gap
PEAD_BONUS_FLOOR = 0             # bonus once we're past the drift window
SKIP_NEGATIVE_PEAD = True         # long-only strategy → ignore negative-gap setups


@dataclass
class PEADSetup:
    """Active PEAD setup: a recent earnings-like gap with drift potential."""
    symbol: str
    event_date: date              # day the gap happened (likely earnings)
    direction: str                 # "positive" or "negative"
    gap_pct: float                # the original opening gap
    confirmation_close_pct: float  # how the gap-day closed (vs prev close)
    volume_ratio: float           # gap-day volume / 20d avg
    days_since_event: int         # trading days elapsed since gap
    drift_window_remaining: int   # = DRIFT_WINDOW_DAYS - days_since_event
    is_active: bool                # within drift window?

    def __str__(self) -> str:
        return (
            f"PEAD[{self.symbol}] {self.direction} gap {self.gap_pct:+.1f}% "
            f"day +{self.days_since_event}/{DRIFT_WINDOW_DAYS}"
        )


def find_active_pead_setup_in_df(
    bars,  # pandas.DataFrame (typed loosely to avoid the import at module-load)
    bar_index: int,
    symbol: str = "",
) -> PEADSetup | None:
    """Point-in-time PEAD detector for backtests.

    Operates on a pre-loaded DataFrame `bars` and only looks at the slice
    [0:bar_index+1], so it never peeks at future data.

    The detection rules mirror find_active_pead_setup() so production and
    backtest use identical logic.
    """
    if bars is None or bar_index < 25 or bar_index >= len(bars):
        return None

    try:
        import pandas as pd  # local import — backtest path always has pandas
    except Exception:
        return None

    df = bars.iloc[: bar_index + 1].copy()
    df = df.sort_values("timestamp").reset_index(drop=True)

    df["prev_close"] = df["close"].shift(1)
    df["gap_pct"] = (df["open"] - df["prev_close"]) / df["prev_close"] * 100
    df["close_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
    df["avg_vol_20d"] = df["volume"].rolling(20).mean().shift(1)

    scan_window = df.tail(LOOKBACK_DAYS).iloc[::-1]
    for _, row in scan_window.iterrows():
        prev_close = row.get("prev_close")
        avg_vol = row.get("avg_vol_20d")
        gap_pct = row.get("gap_pct")
        close_pct = row.get("close_pct")
        bar_date = row["timestamp"].date() if hasattr(row["timestamp"], "date") else None
        volume = row.get("volume", 0)

        if (
            prev_close is None
            or avg_vol is None
            or gap_pct is None
            or bar_date is None
            or pd.isna(avg_vol)
            or avg_vol < MIN_VOLUME_20D
        ):
            continue
        if abs(gap_pct) < GAP_THRESHOLD_PCT:
            continue
        if volume < VOLUME_MULTIPLE * avg_vol:
            continue

        idx = int(row.name)
        days_since = (len(df) - 1) - idx
        if days_since < 1 or days_since >= DRIFT_WINDOW_DAYS:
            continue

        direction = "positive" if gap_pct > 0 else "negative"
        if SKIP_NEGATIVE_PEAD and direction == "negative":
            continue
        if direction == "positive" and close_pct < 0:
            continue
        if direction == "negative" and close_pct > 0:
            continue

        return PEADSetup(
            symbol=symbol.upper(),
            event_date=bar_date,
            direction=direction,
            gap_pct=float(gap_pct),
            confirmation_close_pct=float(close_pct),
            volume_ratio=float(volume / avg_vol),
            days_since_event=int(days_since),
            drift_window_remaining=int(DRIFT_WINDOW_DAYS - days_since),
            is_active=True,
        )

    return None


def find_active_pead_setup(
    symbol: str,
    today: date | None = None,
) -> PEADSetup | None:
    """Return the most recent active PEAD setup for *symbol* or None.

    Scans the last LOOKBACK_DAYS bars for the most recent gap day matching the
    PEAD criteria. Returns None when the KB is empty, no qualifying gap is
    found, or all qualifying gaps have already exited the drift window.
    """
    today = today or date.today()
    sym = symbol.upper()

    try:
        from shark.data.knowledge_base import load_historical_bars
    except Exception as exc:
        logger.debug("pead: knowledge_base import failed: %s", exc)
        return None

    bars = load_historical_bars(sym)
    if bars.empty or len(bars) < 25:
        return None

    bars = bars.sort_values("timestamp").reset_index(drop=True)
    # Compute prev-close, gap, and 20-day avg volume
    bars["prev_close"] = bars["close"].shift(1)
    bars["gap_pct"] = (bars["open"] - bars["prev_close"]) / bars["prev_close"] * 100
    bars["close_pct"] = (bars["close"] - bars["prev_close"]) / bars["prev_close"] * 100
    bars["avg_vol_20d"] = bars["volume"].rolling(20).mean().shift(1)

    # Iterate the most recent LOOKBACK_DAYS in reverse — return first match
    scan_window = bars.tail(LOOKBACK_DAYS).iloc[::-1]
    best: PEADSetup | None = None
    for _, row in scan_window.iterrows():
        prev_close = row.get("prev_close")
        avg_vol = row.get("avg_vol_20d")
        gap_pct = row.get("gap_pct")
        close_pct = row.get("close_pct")
        bar_date = row["timestamp"].date() if hasattr(row["timestamp"], "date") else None
        volume = row.get("volume", 0)

        if (
            prev_close is None
            or avg_vol is None
            or gap_pct is None
            or bar_date is None
            or avg_vol < MIN_VOLUME_20D
        ):
            continue

        if abs(gap_pct) < GAP_THRESHOLD_PCT:
            continue
        if volume < VOLUME_MULTIPLE * avg_vol:
            continue

        # How many trading days since this gap? Use bar index distance.
        idx = int(row.name) if hasattr(row, "name") else None
        if idx is None:
            continue
        days_since = (len(bars) - 1) - idx
        if days_since < 1 or days_since >= DRIFT_WINDOW_DAYS:
            continue

        direction = "positive" if gap_pct > 0 else "negative"
        # For long-only deployments we skip negative-direction setups
        if SKIP_NEGATIVE_PEAD and direction == "negative":
            continue

        # Confirmed setup: gap-day close also moved in the same direction as the gap
        # (avoids "gap and go reverse" pump-and-dumps).
        if direction == "positive" and close_pct < 0:
            continue
        if direction == "negative" and close_pct > 0:
            continue

        best = PEADSetup(
            symbol=sym,
            event_date=bar_date,
            direction=direction,
            gap_pct=float(gap_pct),
            confirmation_close_pct=float(close_pct),
            volume_ratio=float(volume / avg_vol),
            days_since_event=int(days_since),
            drift_window_remaining=int(DRIFT_WINDOW_DAYS - days_since),
            is_active=True,
        )
        break  # most recent qualifying gap wins

    return best


def compute_pead_score_bonus(setup: PEADSetup | None) -> int:
    """Compute integer score bonus for a PEAD setup with linear time decay.

    Day 1  → PEAD_BONUS_AT_DAY_1 (e.g. +6)
    Day 60 → PEAD_BONUS_FLOOR (0)
    Outside window → 0
    Negative setups (long-only) → 0
    """
    if setup is None or not setup.is_active:
        return 0
    if SKIP_NEGATIVE_PEAD and setup.direction == "negative":
        return 0

    # Linear decay
    progress = setup.days_since_event / max(DRIFT_WINDOW_DAYS, 1)
    progress = max(0.0, min(progress, 1.0))
    bonus = (1.0 - progress) * (PEAD_BONUS_AT_DAY_1 - PEAD_BONUS_FLOOR) + PEAD_BONUS_FLOOR
    return int(round(bonus))


def save_pead_setup(setup: PEADSetup) -> None:
    """Persist an active PEAD setup to kb/earnings/{symbol}_{event_date}.json
    so the daily summary / weekly review can audit decisions later.
    """
    try:
        from shark.data.knowledge_base import _EARNINGS_DIR, _write_json
    except Exception as exc:
        logger.debug("pead: cannot persist setup, KB import failed: %s", exc)
        return

    payload: dict[str, Any] = {
        "symbol": setup.symbol,
        "event_date": setup.event_date.isoformat(),
        "direction": setup.direction,
        "gap_pct": round(setup.gap_pct, 4),
        "confirmation_close_pct": round(setup.confirmation_close_pct, 4),
        "volume_ratio": round(setup.volume_ratio, 2),
        "days_since_event": setup.days_since_event,
        "drift_window_remaining": setup.drift_window_remaining,
    }
    out_path = _EARNINGS_DIR / f"{setup.symbol}_{setup.event_date.isoformat()}.json"
    _write_json(out_path, payload)
