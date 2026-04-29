"""
Tests for the multi-agent enhancements:
  - Pydantic schemas (Priority 2)
  - Debate orchestrator (Priority 1)
  - Risk debate (Priority 3)
  - Outcome resolver (Priority 4)
  - Multi-provider LLM client (Priority 5)
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Priority 2: Schemas ──────────────────────────────────────────────

class TestSchemas:
    def test_bull_thesis_valid(self):
        from shark.agents.schemas import BullThesis, render_bull_thesis
        thesis = BullThesis(
            symbol="AAPL",
            thesis="Apple shows strong momentum with Q4 earnings beat.",
            catalysts=["iPhone 16 launch", "Services revenue growth"],
            target_price=195.0,
            entry_zone={"low": 180.0, "high": 185.0},
            timeframe_days=10,
            confidence=0.82,
            supporting_data="RSI 65, above SMA20",
        )
        d = render_bull_thesis(thesis)
        assert d["symbol"] == "AAPL"
        assert d["confidence"] == 0.82
        assert len(d["catalysts"]) == 2

    def test_bull_thesis_confidence_bounds(self):
        from shark.agents.schemas import BullThesis
        with pytest.raises(Exception):
            BullThesis(
                symbol="X", thesis="", catalysts=[], target_price=10,
                entry_zone={}, timeframe_days=1, confidence=1.5,
                supporting_data="",
            )

    def test_bear_thesis_valid(self):
        from shark.agents.schemas import BearThesis, render_bear_thesis
        thesis = BearThesis(
            symbol="TSLA",
            counter_thesis="Tesla faces margin compression from price cuts.",
            risks=["EV competition", "Margin erosion"],
            downside_target=150.0,
            stop_recommended=175.0,
            invalidation_signal="Break above $200 with volume",
            confidence=0.7,
        )
        d = render_bear_thesis(thesis)
        assert d["symbol"] == "TSLA"
        assert len(d["risks"]) == 2

    def test_trade_decision_valid(self):
        from shark.agents.schemas import TradeDecision, TradeAction, render_trade_decision
        dec = TradeDecision(
            decision=TradeAction.BUY,
            symbol="NVDA",
            confidence=0.85,
            position_size_pct=5.0,
            entry_price=450.0,
            stop_loss=430.0,
            target_price=500.0,
            risk_reward_ratio=2.5,
            reasoning="Bull thesis outweighs bear concerns.",
            thesis_summary="BUY NVDA — AI chip demand accelerating",
        )
        d = render_trade_decision(dec)
        assert d["decision"] == "BUY"
        assert d["confidence"] == 0.85

    def test_outcome_reflection_render(self):
        from shark.agents.schemas import OutcomeReflection, render_outcome_reflection
        ref = OutcomeReflection(
            symbol="AAPL",
            trade_date="2024-01-15",
            raw_return_pct=5.2,
            alpha_vs_spy_pct=3.1,
            holding_days=7,
            directional_correct=True,
            thesis_assessment="Thesis held — earnings beat drove move.",
            lesson="Continue targeting PEAD setups with RS > 1.2.",
        )
        text = render_outcome_reflection(ref)
        assert "AAPL" in text
        assert "+5.2%" in text
        assert "+3.1%" in text

    def test_pydantic_to_claude_tool(self):
        from shark.agents.schemas import BullThesis, pydantic_to_claude_tool
        tool = pydantic_to_claude_tool(BullThesis, "bull_thesis", "Generate bull thesis")
        assert tool["name"] == "bull_thesis"
        assert "input_schema" in tool
        assert "properties" in tool["input_schema"]


# ── Priority 1: Debate Orchestrator ──────────────────────────────────

class TestDebateOrchestrator:
    def test_no_debate_result(self):
        from shark.agents.debate_orchestrator import _no_debate_result
        result = _no_debate_result("AAPL", "test reason")
        assert result["decision"]["decision"] == "NO_TRADE"
        assert result["bull"]["confidence"] == 0.0
        assert result["debate_rounds"] == 0

    def test_risk_check_fails_returns_no_trade(self):
        from shark.agents.debate_orchestrator import run_debate
        result = run_debate(
            symbol="AAPL",
            market_data={},
            perplexity_intel={},
            risk_check={"approved": False, "violations": ["max positions exceeded"]},
            rounds=1,
        )
        assert result["decision"]["decision"] == "NO_TRADE"
        assert "max positions" in result["decision"]["reasoning"]

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_no_api_key_falls_back(self):
        """Without API key, debate should fall back gracefully."""
        from shark.agents.debate_orchestrator import run_debate
        result = run_debate(
            symbol="AAPL",
            market_data={"current_price": 180},
            perplexity_intel={},
            risk_check={"approved": True},
            rounds=1,
        )
        # Should get some result (either fallback or error)
        assert "decision" in result


# ── Priority 3: Risk Debate ──────────────────────────────────────────

class TestRiskDebate:
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_no_api_key_skips(self):
        from shark.agents.risk_debate import run_risk_debate
        result = run_risk_debate(
            symbol="AAPL",
            trade_decision={"decision": "BUY", "confidence": 0.8},
            market_data={},
        )
        assert result["approved"] is True
        assert "Skipped" in result["debate_summary"]


# ── Priority 4: Outcome Resolver ─────────────────────────────────────

class TestOutcomeResolver:
    def test_fetch_returns(self):
        from shark.agents.outcome_resolver import _fetch_returns
        returns = _fetch_returns(
            symbol="AAPL",
            entry_date="2024-01-10",
            exit_date="2024-01-17",
            entry_price=180.0,
            exit_price=190.0,
        )
        assert returns["raw_return_pct"] == pytest.approx(5.56, abs=0.01)
        assert "alpha_vs_spy_pct" in returns
        assert "holding_days" in returns
        assert returns["holding_days"] == 7

    def test_template_reflection(self):
        from shark.agents.outcome_resolver import _template_reflection
        trade = {"exit_reason": "target"}
        returns = {"raw_return_pct": 8.5, "alpha_vs_spy_pct": 5.2}
        text = _template_reflection(trade, returns)
        assert "correct" in text
        assert "+8.5%" in text

    def test_pending_store_and_load(self, tmp_path):
        from shark.agents import outcome_resolver
        # Redirect pending file to temp
        original = outcome_resolver._PENDING_FILE
        outcome_resolver._PENDING_FILE = tmp_path / "pending.json"

        try:
            outcome_resolver.store_pending_outcome(
                symbol="AAPL",
                entry_date="2024-01-10",
                entry_price=180.0,
                trade_decision={"thesis_summary": "Test thesis"},
            )
            pending = outcome_resolver._load_pending()
            assert len(pending) == 1
            assert pending[0]["symbol"] == "AAPL"

            outcome_resolver._remove_from_pending("AAPL", "2024-01-10")
            pending = outcome_resolver._load_pending()
            assert len(pending) == 0
        finally:
            outcome_resolver._PENDING_FILE = original


# ── Priority 5: Multi-Provider LLM Client ────────────────────────────

class TestLLMClient:
    def test_llm_response_to_json(self):
        from shark.llm.client import LLMResponse
        r = LLMResponse('{"key": "value"}', "test-model")
        assert r.to_json() == {"key": "value"}

    def test_llm_response_to_json_with_fences(self):
        from shark.llm.client import LLMResponse
        r = LLMResponse('```json\n{"key": "value"}\n```', "test-model")
        assert r.to_json() == {"key": "value"}

    def test_llm_response_bad_json(self):
        from shark.llm.client import LLMResponse
        r = LLMResponse("not json", "test-model")
        assert r.to_json() is None

    def test_unknown_provider_raises(self):
        from shark.llm.client import get_llm_client
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm_client(provider="nonexistent")

    @patch.dict(os.environ, {"SHARK_LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test"})
    def test_anthropic_client_creation(self):
        """Test that AnthropicClient can be instantiated with API key."""
        try:
            from shark.llm.client import AnthropicClient
            client = AnthropicClient(model="claude-sonnet-4-6", api_key="test-key")
            assert client.provider_name == "anthropic"
            assert client.model == "claude-sonnet-4-6"
        except ImportError:
            pytest.skip("anthropic not installed")


# ── Config: new debate settings ──────────────────────────────────────

class TestConfigDebateSettings:
    @patch.dict(os.environ, {
        "SHARK_DEBATE_ROUNDS": "2",
        "SHARK_LLM_RISK_REVIEW": "true",
        "SHARK_RISK_DEBATE_ROUNDS": "1",
        "ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test",
    })
    def test_debate_settings_loaded(self):
        from shark.config import load_settings
        s = load_settings(force_reload=True)
        assert s.debate_rounds == 2
        assert s.llm_risk_review is True
        assert s.risk_debate_rounds == 1

    @patch.dict(os.environ, {
        "SHARK_DEBATE_ROUNDS": "0",
        "SHARK_LLM_RISK_REVIEW": "false",
        "ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test",
    })
    def test_debate_disabled(self):
        from shark.config import load_settings
        s = load_settings(force_reload=True)
        assert s.debate_rounds == 0
        assert s.llm_risk_review is False
