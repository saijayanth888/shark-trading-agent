"""
KB Refresh Phase — Sunday 8 AM ET full rebuild.

Heavy operation that runs once per week:
  1. Refresh S&P 500 constituents list from upstream
  2. Pull 504 daily bars (~2 years) for all S&P 500 tickers + sector ETFs + SPY
  3. Save bars to kb/historical_bars/{TICKER}.json
  4. Re-extract all statistical patterns (calendar, sector, regime, anti-patterns)
  5. Auto-commit + push the kb/ folder

Designed to run as a Cloud Routine on Sundays when markets are closed.
Total runtime: ~10-15 minutes for 500+ tickers.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def run(dry_run: bool = False) -> bool:
    """Phase entry point — invoked by shark/run.py.

    Returns True on success, False on hard failure.
    Honours dry_run by skipping git push (still pulls + writes locally).
    """
    started_at = datetime.utcnow()
    logger.info("=" * 60)
    logger.info("KB REFRESH — Sunday full rebuild")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # 1) Refresh S&P 500 constituents
    # ------------------------------------------------------------------
    try:
        from shark.data.sp500 import refresh_sp500_cache, get_sp500_tickers
        cache = refresh_sp500_cache()
        sp500 = get_sp500_tickers()
        logger.info("S&P 500 cache refreshed — %d tickers", len(sp500))
    except Exception as exc:
        logger.error("S&P 500 refresh failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # 2) Build full universe = S&P 500 + sector ETFs + SPY
    # ------------------------------------------------------------------
    from shark.data.watchlist import SECTOR_ETFS

    sector_etfs = list(SECTOR_ETFS.values())
    universe = sorted(set(sp500 + sector_etfs + ["SPY", "QQQ", "IWM", "DIA"]))
    logger.info("Universe size: %d (S&P 500 + sector ETFs + indices)", len(universe))

    # ------------------------------------------------------------------
    # 3) Smart classification — only fetch what's actually needed
    #    - NEW tickers (not in KB or KB has < 100 bars): pull full 504 bars
    #    - STALE tickers (last bar > 30 days old): pull full 504 bars
    #    - FRESH tickers (have recent data): pull last 10 bars (delta only)
    #    Steady-state cost: ~10 bars × ~520 tickers = ~5K bars (vs 262K brute force)
    # ------------------------------------------------------------------
    from shark.data.knowledge_base import (
        load_historical_bars, save_historical_bars,
        save_bars_metadata, merge_bars,
    )

    needs_full_pull: list[str] = []
    needs_delta_pull: list[str] = []
    today_dt = date.today()

    for sym in universe:
        existing = load_historical_bars(sym)
        if existing.empty or len(existing) < 100:
            needs_full_pull.append(sym)
            continue
        try:
            last_bar_date = existing["timestamp"].max().date()
        except Exception:
            needs_full_pull.append(sym)
            continue
        days_since = (today_dt - last_bar_date).days
        if days_since > 30:
            needs_full_pull.append(sym)
        else:
            needs_delta_pull.append(sym)

    logger.info(
        "Classified universe: %d full pulls (new/stale), %d delta pulls (incremental)",
        len(needs_full_pull), len(needs_delta_pull),
    )

    # ------------------------------------------------------------------
    # 4) Fetch + persist
    # ------------------------------------------------------------------
    from shark.data.alpaca_data import get_bars_multi

    saved_count = 0
    skipped: list[str] = []

    # 4a) Full pulls — overwrite the file with fresh 504 bars
    if needs_full_pull:
        try:
            full_bars = get_bars_multi(
                symbols=needs_full_pull,
                timeframe="1Day",
                limit=504,
                batch_size=100,
            )
            logger.info("Full pull: got %d / %d tickers", len(full_bars), len(needs_full_pull))
        except Exception as exc:
            logger.error("Full bar fetch failed: %s", exc)
            full_bars = {}
        for sym, df in full_bars.items():
            if df is None or df.empty:
                skipped.append(sym)
                continue
            try:
                save_historical_bars(sym, df)
                saved_count += 1
            except Exception as exc:
                logger.warning("Failed to save full bars for %s: %s", sym, exc)
                skipped.append(sym)

    # 4b) Delta pulls — fetch last ~10 bars and merge into existing
    if needs_delta_pull:
        try:
            delta_bars = get_bars_multi(
                symbols=needs_delta_pull,
                timeframe="1Day",
                limit=10,
                batch_size=100,
            )
            logger.info("Delta pull: got %d / %d tickers", len(delta_bars), len(needs_delta_pull))
        except Exception as exc:
            logger.error("Delta bar fetch failed: %s", exc)
            delta_bars = {}
        for sym, fresh_df in delta_bars.items():
            if fresh_df is None or fresh_df.empty:
                # Not necessarily an error — ticker simply had no new bars (e.g. low volume)
                continue
            try:
                existing = load_historical_bars(sym)
                merged = merge_bars(existing, fresh_df)
                # Trim to the last 504 bars (~2 years) to bound storage growth
                if len(merged) > 504:
                    merged = merged.tail(504).reset_index(drop=True)
                save_historical_bars(sym, merged)
                saved_count += 1
            except Exception as exc:
                logger.warning("Failed to save delta bars for %s: %s", sym, exc)
                skipped.append(sym)

    save_bars_metadata({
        "last_refresh": started_at.isoformat() + "Z",
        "ticker_count": saved_count,
        "feed": os.environ.get("ALPACA_DATA_FEED", "iex"),
        "universe_size": len(universe),
        "full_pulls": len(needs_full_pull),
        "delta_pulls": len(needs_delta_pull),
        "skipped_count": len(skipped),
    })
    logger.info("Saved bars: %d  |  skipped: %d", saved_count, len(skipped))
    if skipped[:10]:
        logger.info("First skipped: %s", ", ".join(skipped[:10]))

    # ------------------------------------------------------------------
    # 5) Re-extract all patterns
    # ------------------------------------------------------------------
    try:
        from scripts.extract_patterns import extract_all_patterns
        stats = extract_all_patterns()
        logger.info("Pattern extraction: %s", stats)
    except Exception as exc:
        logger.error("Pattern extraction failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # 6) Auto-commit + push kb/ folder (skip when dry_run)
    # ------------------------------------------------------------------
    if not dry_run:
        try:
            _git_commit_push(started_at, saved_count, len(skipped))
        except Exception as exc:
            logger.error("Git commit/push failed: %s", exc)
            # Don't fail the phase — the bars are saved locally either way.

    duration = (datetime.utcnow() - started_at).total_seconds()
    logger.info("=" * 60)
    logger.info("KB REFRESH COMPLETE — %d tickers in %.1fs", saved_count, duration)
    logger.info("=" * 60)
    return True


def _git_commit_push(started_at: datetime, saved: int, skipped: int) -> None:
    """Commit kb/ changes and push to origin/main."""
    cwd = str(_REPO_ROOT)
    today = date.today().isoformat()

    # Check for changes
    status = subprocess.run(
        ["git", "status", "--porcelain", "kb/"],
        cwd=cwd, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        logger.info("Git: no kb/ changes to commit")
        return

    subprocess.run(["git", "add", "kb/"], cwd=cwd, check=True)
    msg = (
        f"kb-refresh: weekly rebuild {today}\n\n"
        f"- Tickers saved: {saved}\n"
        f"- Tickers skipped: {skipped}\n"
        f"- Started: {started_at.isoformat()}Z"
    )
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=cwd, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=cwd, check=True, capture_output=True,
    )
    logger.info("Git: kb/ changes pushed to main")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(0 if run() else 1)
