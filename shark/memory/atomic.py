"""
Atomic file writes — partial-write-safe helpers for the memory/ layer.

Why: every state file (PROJECT-CONTEXT.md, open-trades.json, kb/* JSON)
was written via Path.write_text() / json.dump(), which leaves a partial
file on disk if the process is killed mid-write. Subsequent reads then
fail parsing and silently fall back to defaults — losing circuit-breaker
state, open-trade attribution, or PEAD outcomes.

Pattern:
    1. Write the new content to a temp file in the same directory.
    2. fsync() the temp file (so its bytes are durable on disk).
    3. os.replace() the temp file over the destination — POSIX guarantees
       this is an atomic rename within the same filesystem.

For multi-writer safety (e.g. two cloud routines running simultaneously),
also wrap mutating operations in a directory-level fcntl lock via the
`file_lock` context manager.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically.

    Crash safety:
        After a successful return, *path* contains either the previous
        bytes (if the process was killed before os.replace) or the new
        bytes — never a partial mix. Concurrent readers always see one
        or the other.

    Raises:
        OSError: if the directory cannot be created, the temp file cannot
            be written, or os.replace fails.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create the temp file in the same directory so os.replace stays
    # within one filesystem (cross-fs replace is not atomic on Linux).
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup of the temp file on failure
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    """Atomically serialize *data* as JSON into *path*."""
    payload = json.dumps(data, indent=indent, default=str)
    atomic_write_text(path, payload + ("\n" if indent is not None else ""))


@contextlib.contextmanager
def file_lock(lock_path: Path, *, timeout_seconds: float = 30.0) -> Iterator[None]:
    """Process-level advisory file lock for cross-routine mutual exclusion.

    Holds an exclusive (LOCK_EX) flock on *lock_path* (created if needed)
    for the duration of the with-block. Other processes attempting to
    acquire the same lock will block.

    The lock file is *not* deleted after release — it's a persistent
    coordination point. Deleting it concurrently with another acquirer
    is a race we deliberately avoid.

    Args:
        lock_path: Path to the lock file (e.g. memory/.open-trades.lock).
        timeout_seconds: Max seconds to wait before giving up. On
            timeout, raises TimeoutError; the caller decides whether to
            retry, fail, or skip the operation.

    Raises:
        TimeoutError: if the lock cannot be acquired within timeout.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = None
    if timeout_seconds is not None:
        import time
        deadline = time.monotonic() + timeout_seconds

    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if deadline is None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    break
                import time
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire lock {lock_path} within "
                        f"{timeout_seconds:.1f}s"
                    ) from exc
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


__all__ = [
    "atomic_write_text",
    "atomic_write_json",
    "file_lock",
]
