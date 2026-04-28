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
    # 3) Pull 504 daily bars per ticker (~2 years)
    # ------------------------------------------------------------------
    try:
        from shark.data.alpaca_data import get_bars_multi
        bars_by_symbol = get_bars_multi(
            symbols=universe,
            timeframe="1Day",
            limit=504,
            batch_size=100,
        )
        logger.info("Pulled bars for %d / %d tickers", len(bars_by_symbol), len(universe))
    except Exception as exc:
        logger.error("Bar fetch failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # 4) Persist bars to KB
    # ------------------------------------------------------------------
    from shark.data.knowledge_base import save_historical_bars, save_bars_metadata

    saved_count = 0
    skipped: list[str] = []
    for sym, df in bars_by_symbol.items():
        if df is None or df.empty:
            skipped.append(sym)
            continue
        try:
            save_historical_bars(sym, df)
            saved_count += 1
        except Exception as exc:
            logger.warning("Failed to save bars for %s: %s", sym, exc)
            skipped.append(sym)

    save_bars_metadata({
        "last_refresh": started_at.isoformat() + "Z",
        "ticker_count": saved_count,
        "feed": os.environ.get("ALPACA_DATA_FEED", "iex"),
        "universe_size": len(universe),
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
