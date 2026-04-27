import argparse
import importlib
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

from shark.context.context_manager import generate_context_briefing, check_context_health

PHASES = {
    "pre-market": "shark.phases.pre_market",
    "pre-execute": "shark.phases.pre_execute",
    "market-open": "shark.phases.market_open",
    "midday": "shark.phases.midday",
    "daily-summary": "shark.phases.daily_summary",
    "weekly-review": "shark.phases.weekly_review",
    "backtest": "shark.phases.backtest",
}

_LOG_FILE = Path(__file__).resolve().parents[1] / "memory" / "error.log"

logger = logging.getLogger(__name__)


def _load_env() -> None:
    # Cloud routines: env vars are injected by the cloud environment — nothing to load.
    # Local dev only: if a .env file exists, load it WITHOUT overriding already-set vars.
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return  # cloud path — all vars already in os.environ
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())  # never overrides cloud vars


def _setup_logging() -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout)

    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(file_handler)


def _sync_repo() -> None:
    """Pull latest main so cloud containers pick up memory from previous routines."""
    repo_root = Path(__file__).resolve().parents[1]
    try:
        subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        logger.info("git pull --rebase completed")
    except Exception as exc:
        logger.warning("git sync skipped: %s", exc)


def _run_phase(phase: str, dry_run: bool) -> bool:
    module_path = PHASES[phase]
    mod = importlib.import_module(module_path)
    return mod.run(dry_run=dry_run)


def main() -> None:
    _load_env()
    _setup_logging()

    parser = argparse.ArgumentParser(
        prog="shark",
        description="Shark trading agent — phase runner",
    )
    parser.add_argument(
        "phase",
        choices=list(PHASES.keys()),
        help="Trading phase to run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run phase logic without writing to memory or placing orders",
    )
    args = parser.parse_args()

    logger.info("=== shark run.py starting phase=%s dry_run=%s ===", args.phase, args.dry_run)
    _sync_repo()

    # Generate phase-specific context briefing BEFORE execution
    try:
        briefing_path = generate_context_briefing(args.phase)
        logger.info("Context briefing ready: %s", briefing_path)
        health = check_context_health()
        if health.get("over_budget"):
            logger.warning("CONTEXT HEALTH: memory files exceed safe token threshold — consider archiving")
    except Exception:
        logger.warning("Context briefing generation failed — phase will proceed without it")

    try:
        success = _run_phase(args.phase, dry_run=args.dry_run)
    except Exception:
        tb = traceback.format_exc()
        logger.error("Unhandled exception in phase %s:\n%s", args.phase, tb)
        print(f"ERROR: phase '{args.phase}' failed — see memory/error.log for details", file=sys.stderr)
        sys.exit(1)

    if success:
        logger.info("=== phase=%s completed successfully ===", args.phase)
        sys.exit(0)
    else:
        logger.error("=== phase=%s returned failure ===", args.phase)
        sys.exit(1)


main()
