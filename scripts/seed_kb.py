"""
One-shot KB seeder — bootstrap the Knowledge Base from scratch.

Equivalent to running the kb-refresh phase locally. Use this once to
populate kb/historical_bars/ with 2 years of S&P 500 data before the
weekly Cloud Routine takes over.

Usage:
    # From repo root:
    export ALPACA_API_KEY=...
    export ALPACA_SECRET_KEY=...
    export ALPACA_DATA_FEED=iex     # or sip if you have paid subscription
    python scripts/seed_kb.py

Without `--commit`, no git push occurs (safe for local exploration).
With `--commit`, behavior matches the Cloud Routine (auto-push to main).

Estimated runtime: 10-15 minutes for 503 S&P 500 + 11 sector ETFs + indices.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Shark KB")
    parser.add_argument(
        "--limit",
        type=int,
        default=504,
        help="Number of daily bars per ticker (default: 504 ≈ 2 years)",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="Cap on universe size (0 = no cap, full S&P 500)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="git add + commit + push after seeding (default: off)",
    )
    parser.add_argument(
        "--skip-patterns",
        action="store_true",
        help="Don't run pattern extraction (faster for first-time seed)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    started_at = datetime.utcnow()
    logging.info("=" * 60)
    logging.info("KB SEED — Bootstrapping Knowledge Base")
    logging.info("=" * 60)

    # 1) Refresh S&P 500 list
    from shark.data.sp500 import refresh_sp500_cache, get_sp500_tickers
    refresh_sp500_cache()
    sp500 = get_sp500_tickers()
    logging.info("S&P 500 cache: %d tickers", len(sp500))

    # 2) Build universe
    from shark.data.watchlist import SECTOR_ETFS
    universe = sorted(set(
        sp500 + list(SECTOR_ETFS.values()) + ["SPY", "QQQ", "IWM", "DIA"]
    ))
    if args.max_tickers > 0:
        universe = universe[: args.max_tickers]
    logging.info("Universe size: %d", len(universe))

    # 3) Pull bars
    from shark.data.alpaca_data import get_bars_multi
    logging.info("Fetching %d bars per ticker (~%d API batches)...",
                 args.limit, (len(universe) + 99) // 100)
    bars = get_bars_multi(universe, timeframe="1Day", limit=args.limit, batch_size=100)
    logging.info("Got bars for %d / %d tickers", len(bars), len(universe))

    # 4) Save to KB
    from shark.data.knowledge_base import save_historical_bars, save_bars_metadata

    saved = 0
    skipped: list[str] = []
    for sym, df in bars.items():
        if df is None or df.empty:
            skipped.append(sym)
            continue
        try:
            save_historical_bars(sym, df)
            saved += 1
        except Exception as exc:
            logging.warning("Failed %s: %s", sym, exc)
            skipped.append(sym)

    save_bars_metadata({
        "last_refresh": started_at.isoformat() + "Z",
        "ticker_count": saved,
        "feed": os.environ.get("ALPACA_DATA_FEED", "iex"),
        "universe_size": len(universe),
        "skipped_count": len(skipped),
        "source": "seed_kb.py",
    })
    logging.info("Saved bars: %d  |  Skipped: %d", saved, len(skipped))

    # 5) Pattern extraction
    if not args.skip_patterns:
        try:
            from scripts.extract_patterns import extract_all_patterns
            stats = extract_all_patterns()
            logging.info("Patterns extracted: %s", stats)
        except Exception as exc:
            logging.warning("Pattern extraction failed: %s", exc)

    # 6) Optional git commit + push
    if args.commit:
        _git_commit_push(saved, len(skipped))

    duration = (datetime.utcnow() - started_at).total_seconds()
    logging.info("=" * 60)
    logging.info("KB SEED COMPLETE — %d tickers in %.1fs", saved, duration)
    logging.info("=" * 60)
    return 0


def _git_commit_push(saved: int, skipped: int) -> None:
    cwd = str(_REPO_ROOT)
    status = subprocess.run(
        ["git", "status", "--porcelain", "kb/"],
        cwd=cwd, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        logging.info("Git: no kb/ changes to commit")
        return
    subprocess.run(["git", "add", "kb/"], cwd=cwd, check=True)
    msg = f"kb-seed: bootstrap {saved} tickers ({skipped} skipped)"
    subprocess.run(["git", "commit", "-m", msg], cwd=cwd, check=True)
    subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=cwd, check=True,
    )
    logging.info("Git: kb/ pushed to main")


if __name__ == "__main__":
    raise SystemExit(main())
