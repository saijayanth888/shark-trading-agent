import argparse
import importlib
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

# Ensure repo root is on sys.path so `shark` package resolves when this
# script is invoked directly (e.g. `python shark/run.py <phase>`).
sys.path.insert(0, str(Path(__file__).parent.parent))

# Auto-install using THIS interpreter — critical for cloud sandbox environments
# where `python -m pip install` in bash may target a different Python than the runner.
_req = Path(__file__).resolve().parents[1] / "requirements.txt"
if _req.exists():
    _pip_result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "-q",
            "--no-cache-dir",
            "--prefer-binary",
            "--break-system-packages",
            "-r", str(_req),
        ],
        capture_output=True,
        text=True,
    )
    if _pip_result.returncode != 0:
        print(f"WARNING: pip install failed (exit {_pip_result.returncode})", file=sys.stderr)
        if _pip_result.stderr:
            print(_pip_result.stderr[:500], file=sys.stderr)
        # Fallback: try uv pip install for uv-managed environments
        _uv_result = subprocess.run(
            ["uv", "pip", "install", "-q", "-r", str(_req)],
            capture_output=True,
            text=True,
        )
        if _uv_result.returncode != 0:
            print("WARNING: uv pip install also failed", file=sys.stderr)
            if _uv_result.stderr:
                print(_uv_result.stderr[:500], file=sys.stderr)
        else:
            print("INFO: uv pip install succeeded (pip had failed)", file=sys.stderr)

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

# Phases that require Alpaca credentials and live API access
_TRADING_PHASES = {"market-open", "midday", "pre-execute", "daily-summary"}

_CRITICAL_PACKAGES = {
    "alpaca": "alpaca-py",
    "pandas": "pandas",
    "numpy": "numpy",
}


def _verify_dependencies() -> bool:
    """Verify critical packages are importable. Fails fast before any phase runs."""
    missing = []
    for module_name, pip_name in _CRITICAL_PACKAGES.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        logger.error(
            "FATAL: Required packages not installed: %s — "
            "pip install may have failed silently. "
            "Run manually: pip install %s",
            ", ".join(missing),
            " ".join(missing),
        )
        return False
    return True


def _verify_env_vars(phase: str) -> bool:
    """Verify required environment variables are set for trading phases."""
    if phase not in _TRADING_PHASES:
        return True

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not secret_key:
        logger.error(
            "FATAL: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set for phase '%s'. "
            "Check .env file or cloud environment variable injection.",
            phase,
        )
        return False
    return True


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


def _run_phase(phase: str, dry_run: bool, mode: str = "full") -> bool:
    import inspect
    module_path = PHASES[phase]
    mod = importlib.import_module(module_path)
    if "mode" in inspect.signature(mod.run).parameters:
        return mod.run(dry_run=dry_run, mode=mode)
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
    parser.add_argument(
        "--mode",
        choices=["full", "prepare", "execute"],
        default="full",
        help="full=local dev (default), prepare=cloud data collection, execute=cloud order placement",
    )
    args = parser.parse_args()

    logger.info("=== shark run.py starting phase=%s dry_run=%s ===", args.phase, args.dry_run)
    _sync_repo()

    # Pre-flight checks — fail fast before expensive phase execution
    if not _verify_dependencies():
        print("FATAL: Missing critical dependencies — cannot proceed.", file=sys.stderr)
        sys.exit(1)

    if not _verify_env_vars(args.phase):
        print(f"FATAL: Missing environment variables for phase '{args.phase}'.", file=sys.stderr)
        sys.exit(1)

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
        success = _run_phase(args.phase, dry_run=args.dry_run, mode=args.mode)
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
