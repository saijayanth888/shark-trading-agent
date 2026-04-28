"""
Tests for shark.memory.kill_switch and shark.memory.atomic.
"""

import json
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# atomic writes
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_atomic_write_text(self, tmp_path):
        from shark.memory.atomic import atomic_write_text
        target = tmp_path / "test.txt"
        atomic_write_text(target, "hello world")
        assert target.read_text() == "hello world"

    def test_atomic_write_json(self, tmp_path):
        from shark.memory.atomic import atomic_write_json
        target = tmp_path / "test.json"
        data = {"key": "value", "num": 42}
        atomic_write_json(target, data)
        assert json.loads(target.read_text()) == data

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        from shark.memory.atomic import atomic_write_text
        target = tmp_path / "sub" / "deep" / "file.txt"
        atomic_write_text(target, "nested")
        assert target.read_text() == "nested"

    def test_atomic_overwrite(self, tmp_path):
        from shark.memory.atomic import atomic_write_text
        target = tmp_path / "over.txt"
        atomic_write_text(target, "first")
        atomic_write_text(target, "second")
        assert target.read_text() == "second"

    def test_no_temp_file_left_on_success(self, tmp_path):
        from shark.memory.atomic import atomic_write_text
        target = tmp_path / "clean.txt"
        atomic_write_text(target, "data")
        temps = list(tmp_path.glob(".*clean*.tmp"))
        assert temps == []


# ---------------------------------------------------------------------------
# file_lock
# ---------------------------------------------------------------------------

class TestFileLock:
    def test_lock_creates_lockfile(self, tmp_path):
        from shark.memory.atomic import file_lock
        lock_path = tmp_path / "test.lock"
        with file_lock(lock_path):
            assert lock_path.exists()

    def test_lock_is_reentrant_across_calls(self, tmp_path):
        """Two sequential acquires on the same lock succeed."""
        from shark.memory.atomic import file_lock
        lock_path = tmp_path / "test.lock"
        with file_lock(lock_path):
            pass
        with file_lock(lock_path):
            pass


# ---------------------------------------------------------------------------
# kill switch
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_not_active_when_no_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shark.memory.kill_switch._KILL_FLAG", tmp_path / "KILL.flag")
        from shark.memory.kill_switch import is_killed
        assert is_killed() is False

    def test_active_when_flag_exists(self, tmp_path, monkeypatch):
        flag = tmp_path / "KILL.flag"
        flag.write_text("operator pause: testing")
        monkeypatch.setattr("shark.memory.kill_switch._KILL_FLAG", flag)
        from shark.memory.kill_switch import is_killed
        assert is_killed() is True

    def test_get_reason(self, tmp_path, monkeypatch):
        flag = tmp_path / "KILL.flag"
        flag.write_text("pause for maintenance")
        monkeypatch.setattr("shark.memory.kill_switch._KILL_FLAG", flag)
        from shark.memory.kill_switch import kill_reason
        assert "maintenance" in kill_reason()

    def test_enforce_raises(self, tmp_path, monkeypatch):
        flag = tmp_path / "KILL.flag"
        flag.write_text("stop")
        monkeypatch.setattr("shark.memory.kill_switch._KILL_FLAG", flag)
        from shark.memory.kill_switch import enforce_kill_switch, KillSwitchActive
        with pytest.raises(KillSwitchActive):
            enforce_kill_switch("market_open")

    def test_enforce_does_not_raise_when_inactive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shark.memory.kill_switch._KILL_FLAG", tmp_path / "KILL.flag")
        from shark.memory.kill_switch import enforce_kill_switch
        enforce_kill_switch("market_open")  # should not raise
