import argparse
import importlib
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import traceback
from pathlib import Path

# Ensure repo root is on sys.path so `shark` package resolves when this
# script is invoked directly (e.g. `python shark/run.py <phase>`).
sys.path.insert(0, str(Path(__file__).parent.parent))


def _maybe_install_dependencies() -> None:
    """Install requirements.txt — but only when explicitly opted-in.

    Production deployments should bake dependencies into the container at
    build time, not on every phase run. Set SHARK_AUTO_INSTALL=1 (typically
    only in local-dev sandboxes that lack a build step) to opt in.

    Why this changed: previously this ran unconditionally at module import
    time, which (a) could change runtime behaviour mid-day if requirements
    drifted, and (b) added 10-30s to every phase startup against a slow
    network. Both are unacceptable in a market-hours pipeline.
    """
    if os.environ.get("SHARK_AUTO_INSTALL", "").lower() not in ("1", "true", "yes"):
        return

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    if not req.exists():
        return

    pip_result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "-q",
            "--no-cache-dir",
            "--prefer-binary",
            "--break-system-packages",
            "-r", str(req),
        ],
        capture_output=True,
        text=True,
    )
    if pip_result.returncode != 0:
        print(f"WARNING: pip install failed (exit {pip_result.returncode})", file=sys.stderr)
        if pip_result.stderr:
            print(pip_result.stderr[:500], file=sys.stderr)
        # Fallback: uv pip for uv-managed environments
        uv_result = subprocess.run(
            ["uv", "pip", "install", "-q", "-r", str(req)],
            capture_output=True,
            text=True,
        )
        if uv_result.returncode != 0:
            print("WARNING: uv pip install also failed", file=sys.stderr)
            if uv_result.stderr:
                print(uv_result.stderr[:500], file=sys.stderr)
        else:
            print("INFO: uv pip install succeeded (pip had failed)", file=sys.stderr)

from shark.config import load_settings, ConfigError
from shark.context.context_manager import generate_context_briefing, check_context_health
from shark.memory.kill_switch import enforce_kill_switch, KillSwitchActive

PHASES = {
    "pre-market": "shark.phases.pre_market",
    "pre-execute": "shark.phases.pre_execute",
    "market-open": "shark.phases.market_open",
    "midday": "shark.phases.midday",
    "daily-summary": "shark.phases.daily_summary",
    "weekly-review": "shark.phases.weekly_review",
    "backtest": "shark.phases.backtest",
    "kb-refresh": "shark.phases.kb_refresh",
    "kb-update": "shark.phases.kb_update",
}

_LOG_FILE = Path(__file__).resolve().parents[1] / "memory" / "error.log"

logger = logging.getLogger(__name__)

# Phases that require Alpaca credentials and live API access
# Every phase that touches Alpaca (account, positions, bars, orders)
_TRADING_PHASES = {
    "pre-market", "pre-execute", "market-open",
    "midday", "daily-summary", "weekly-review", "backtest",
    "kb-refresh", "kb-update",
}

# Phases that the operator kill switch (memory/KILL.flag) blocks.
# Research-only phases (kb-refresh, kb-update, backtest) are allowed to run
# while trading is paused so data hygiene continues.
_KILL_SWITCH_PHASES = {
    "pre-market", "pre-execute", "market-open", "midday",
}

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


class _JsonFormatter(logging.Formatter):
    """Structured-JSON log formatter for log-aggregation friendly output.

    Emits one JSON object per record so downstream tooling (Datadog, Loki,
    CloudWatch) can parse fields without regex. Adds a phase_run_id when
    available so events from a single run are correlated.
    """

    def __init__(self, run_id: str | None = None) -> None:
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if self._run_id:
            payload["run_id"] = self._run_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _setup_logging(phase: str | None = None) -> None:
    """Configure stdout + rotating-file logging.

    Set SHARK_LOG_FORMAT=json to emit structured logs (recommended in
    production where logs are consumed by an aggregator). Default stays
    plain text for local readability.
    """
    use_json = os.environ.get("SHARK_LOG_FORMAT", "").lower() == "json"
    text_fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    # Build a per-run correlation id only once per process.
    run_id: str | None = None
    if phase is not None:
        from datetime import datetime
        run_id = f"{phase}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"

    formatter: logging.Formatter
    if use_json:
        formatter = _JsonFormatter(run_id=run_id)
    else:
        formatter = logging.Formatter(text_fmt)

    root = logging.getLogger()
    # Avoid duplicate handlers if _setup_logging is called twice (e.g. in tests).
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(logging.INFO)
    stream.setFormatter(formatter)
    root.addHandler(stream)
    root.setLevel(logging.INFO)

    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _sync_repo() -> None:
    """Pull latest main so cloud containers pick up memory from previous routines.

    Conflict policy: if a clean rebase is impossible we abort and proceed with
    whatever local state we have. We do NOT merge with -X theirs because that
    can silently overwrite uncommitted local memory writes (trade log,
    sidecars, state) that another routine on this host produced. The
    operator-visible PUSH-FAILED.flag check below catches the symmetric case.
    """
    repo_root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("git pull --rebase completed")
            return

        logger.warning(
            "git pull --rebase conflict — aborting and proceeding with local state. "
            "stderr=%s", result.stderr.strip()[:200],
        )
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        logger.warning("git sync skipped: %s", exc)


def _check_push_failed_flag() -> bool:
    """Return True if a previous routine left a PUSH-FAILED.flag we should respect."""
    flag = Path(__file__).resolve().parents[1] / "memory" / "PUSH-FAILED.flag"
    if flag.exists():
        try:
            content = flag.read_text(encoding="utf-8")[:500]
        except OSError:
            content = "(could not read flag)"
        logger.error(
            "PUSH-FAILED.flag present — refusing to run trading phase until "
            "operator resolves the prior memory-sync failure. Contents:\n%s",
            content,
        )
        return True
    return False


def _run_phase(phase: str, dry_run: bool, mode: str = "full") -> bool:
    import inspect
    module_path = PHASES[phase]
    mod = importlib.import_module(module_path)
    if "mode" in inspect.signature(mod.run).parameters:
        return mod.run(dry_run=dry_run, mode=mode)
    return mod.run(dry_run=dry_run)


def main() -> None:
    _load_env()

    # Argparse first so we can include phase in the logger run_id and
    # only auto-install when actually invoked (not on `--help`).
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

    # Now we know the phase — set up logging with a per-run correlation id,
    # and only attempt the opt-in pip install once we're actually running.
    _setup_logging(args.phase)
    _maybe_install_dependencies()

    logger.info("=== shark run.py starting phase=%s dry_run=%s ===", args.phase, args.dry_run)
    _sync_repo()

    # Pre-flight checks — fail fast before expensive phase execution
    if not _verify_dependencies():
        print("FATAL: Missing critical dependencies — cannot proceed.", file=sys.stderr)
        sys.exit(1)

    if not _verify_env_vars(args.phase):
        print(f"FATAL: Missing environment variables for phase '{args.phase}'.", file=sys.stderr)
        sys.exit(1)

    # Validate the central config — any out-of-range tunable fails fast
    # before we touch the broker.
    try:
        settings = load_settings()
        logger.info("Config validated. Knobs: %s", settings.safe_dict())
    except ConfigError as exc:
        print(f"FATAL: invalid configuration — {exc}", file=sys.stderr)
        sys.exit(1)

    # Operator kill switch — refuse to run trading phases while memory/KILL.flag exists.
    if args.phase in _KILL_SWITCH_PHASES:
        try:
            enforce_kill_switch(args.phase)
        except KillSwitchActive as exc:
            # Distinct exit code (75) so cron / cloud routines can recognise an
            # operator pause vs an actual failure and skip alerting.
            print(f"KILL SWITCH: {exc}", file=sys.stderr)
            sys.exit(75)

        # PUSH-FAILED.flag — a prior routine could not push memory and we must
        # not place new orders on top of unsynced state.
        if _check_push_failed_flag():
            print(
                "FATAL: memory/PUSH-FAILED.flag present — operator must resolve "
                "the prior memory-sync failure before trading resumes.",
                file=sys.stderr,
            )
            sys.exit(75)

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


if __name__ == "__main__":
    main()
