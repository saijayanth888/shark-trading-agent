"""
Tests for shark.dashboard.generate — dashboard data generation.
"""

import json
from pathlib import Path

import pytest


class TestDashboardGenerate:
    def test_generates_data_json(self, tmp_path, monkeypatch):
        """Dashboard generator produces valid JSON with expected top-level keys."""
        from shark.dashboard import generate as gen

        # Point all paths to tmp dirs so we don't read real memory/
        monkeypatch.setattr(gen, "_MEMORY_DIR", tmp_path / "memory")
        monkeypatch.setattr(gen, "_KB_DIR", tmp_path / "kb")
        monkeypatch.setattr(gen, "_DASHBOARD_DIR", tmp_path / "dashboard")
        monkeypatch.setattr(gen, "_DATA_PATH", tmp_path / "dashboard" / "data.json")

        (tmp_path / "memory").mkdir()
        (tmp_path / "kb" / "trades").mkdir(parents=True)
        (tmp_path / "kb" / "daily").mkdir(parents=True)

        result = gen.generate_dashboard_data()
        assert result.exists()

        data = json.loads(result.read_text())
        assert "generated_at" in data
        assert "state" in data
        assert "equity_history" in data
        assert "stats" in data
        assert "kill_switch" in data

    def test_parses_equity_history(self, tmp_path, monkeypatch):
        from shark.dashboard import generate as gen

        monkeypatch.setattr(gen, "_MEMORY_DIR", tmp_path)
        monkeypatch.setattr(gen, "_KB_DIR", tmp_path / "kb")
        monkeypatch.setattr(gen, "_DASHBOARD_DIR", tmp_path / "dashboard")
        monkeypatch.setattr(gen, "_DATA_PATH", tmp_path / "dashboard" / "data.json")

        (tmp_path / "kb" / "trades").mkdir(parents=True)
        (tmp_path / "kb" / "daily").mkdir(parents=True)

        trade_log = tmp_path / "TRADE-LOG.md"
        trade_log.write_text(
            "# Trade Log\n\n"
            "### 2026-04-25 — EOD Snapshot\n"
            "**Portfolio:** $100,000.00 | **Cash:** $95,000.00 | **Day P&L:** +500.00\n\n"
            "### 2026-04-26 — EOD Snapshot\n"
            "**Portfolio:** $101,200.00 | **Cash:** $90,000.00 | **Day P&L:** +1200.00\n\n"
        )

        result_path = gen.generate_dashboard_data()
        data = json.loads(result_path.read_text())

        eq = data["equity_history"]
        assert len(eq) == 2
        assert eq[0]["date"] == "2026-04-25"
        assert eq[0]["equity"] == 100000.0
        assert eq[1]["equity"] == 101200.0
        assert eq[1]["day_pnl"] == 1200.0

    def test_kill_switch_detection(self, tmp_path, monkeypatch):
        from shark.dashboard import generate as gen

        monkeypatch.setattr(gen, "_MEMORY_DIR", tmp_path)
        monkeypatch.setattr(gen, "_KB_DIR", tmp_path / "kb")
        monkeypatch.setattr(gen, "_DASHBOARD_DIR", tmp_path / "dashboard")
        monkeypatch.setattr(gen, "_DATA_PATH", tmp_path / "dashboard" / "data.json")

        (tmp_path / "kb" / "trades").mkdir(parents=True)
        (tmp_path / "kb" / "daily").mkdir(parents=True)

        # Create kill flag
        (tmp_path / "KILL.flag").write_text("testing halt")

        result_path = gen.generate_dashboard_data()
        data = json.loads(result_path.read_text())

        assert data["kill_switch"]["active"] is True
        assert "testing halt" in data["kill_switch"]["reason"]

    def test_compute_stats(self):
        from shark.dashboard.generate import _compute_stats

        trades = [
            {"realized_pnl": 500, "r_multiple": 2.0},
            {"realized_pnl": -200, "r_multiple": -0.8},
            {"realized_pnl": 300, "r_multiple": 1.5},
        ]
        equity = [
            {"equity": 100000}, {"equity": 100500},
            {"equity": 100300}, {"equity": 100600},
        ]

        stats = _compute_stats(equity, trades)
        assert stats["total_trades"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["win_rate"] == 66.7
        assert stats["total_pnl"] == 600.0
        assert stats["best_trade"] == 500.0
        assert stats["worst_trade"] == -200.0
