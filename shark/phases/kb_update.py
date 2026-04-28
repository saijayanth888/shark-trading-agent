"""
KB Update Phase — daily 5:30 PM ET incremental update.

Lightweight operation that runs after market close on weekdays:
  1. Append yesterday's bar to each ticker file (incremental, ~1 min)
  2. No pattern recomputation (that runs Sunday)
  3. Auto-commit + push

Total runtime: ~1-2 minutes.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def run(dry_run: bool = False) -> bool:
    """Phase entry point — invoked by shark/run.py.

    Returns True on success. Honours dry_run by skipping git push.
    """
    started_at = datetime.utcnow()
    logger.info("=" * 60)
    logger.info("KB UPDATE — daily incremental")
    logger.info("=" * 60)

    from shark.data.knowledge_base import (
        load_historical_bars,
        save_historical_bars,
        load_bars_metadata,
        save_bars_metadata,
        kb_status,
    )
    from shark.data.alpaca_data import get_bars_multi

    # Discover all tickers currently in KB
    bars_dir = _REPO_ROOT / "kb" / "historical_bars"
    ticker_files = [p for p in bars_dir.glob("*.json") if not p.name.startswith("_")]
    tickers = sorted(p.stem for p in ticker_files)

    if not tickers:
        logger.error("KB has no tickers — run kb-refresh first to seed")
        return False

    logger.info("Updating %d tickers", len(tickers))

    # Pull last 5 bars for each ticker (cheap — covers any missed days)
    fresh = get_bars_multi(symbols=tickers, timeframe="1Day", limit=5, batch_size=100)
    logger.info("Fetched fresh bars for %d / %d tickers", len(fresh), len(tickers))

    updated_count = 0
    skipped: list[str] = []
    for sym, fresh_df in fresh.items():
        if fresh_df is None or fresh_df.empty:
            skipped.append(sym)
            continue
        try:
            existing_df = load_historical_bars(sym)
            merged = _merge_bars(existing_df, fresh_df)
            # Trim to last 504 bars (~2 years) to bound storage
            if len(merged) > 504:
                merged = merged.tail(504).reset_index(drop=True)
            save_historical_bars(sym, merged)
            updated_count += 1
        except Exception as exc:
            logger.warning("Update failed for %s: %s", sym, exc)
            skipped.append(sym)

    # Update metadata
    meta = load_bars_metadata()
    meta["last_update"] = started_at.isoformat() + "Z"
    meta["last_update_count"] = updated_count
    save_bars_metadata(meta)

    logger.info("KB update: %d updated, %d skipped", updated_count, len(skipped))
    logger.info("KB status: %s", kb_status())

    # Commit + push (skip when dry_run)
    if not dry_run:
        try:
            _git_commit_push(started_at, updated_count)
        except Exception as exc:
            logger.error("Git commit/push failed (non-fatal): %s", exc)

    duration = (datetime.utcnow() - started_at).total_seconds()
    logger.info("KB UPDATE COMPLETE — %d tickers in %.1fs", updated_count, duration)
    return True


def _merge_bars(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Merge fresh bars into existing, keeping latest values per date."""
    if existing.empty:
        return fresh.copy()
    if fresh.empty:
        return existing.copy()

    combined = pd.concat([existing, fresh], ignore_index=True)
    # Drop duplicates by date — keep the most recent (last) value
    combined["_date_key"] = pd.to_datetime(combined["timestamp"]).dt.date
    combined = combined.drop_duplicates(subset="_date_key", keep="last")
    combined = combined.drop(columns="_date_key").sort_values("timestamp").reset_index(drop=True)
    return combined


def _git_commit_push(started_at: datetime, updated: int) -> None:
    """Commit kb/ changes and push to origin/main."""
    cwd = str(_REPO_ROOT)
    today = date.today().isoformat()

    status = subprocess.run(
        ["git", "status", "--porcelain", "kb/"],
        cwd=cwd, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        logger.info("Git: no kb/ changes to commit")
        return

    subprocess.run(["git", "add", "kb/"], cwd=cwd, check=True)
    msg = f"kb-update: daily incremental {today} (+{updated} tickers)"
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
