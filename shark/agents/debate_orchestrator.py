"""
Debate Orchestrator — runs N rounds of adversarial bull↔bear debate.

Inspired by TradingAgents' multi-round debate architecture. Each round:
  1. Bull analyst argues FOR the trade, seeing the bear's last argument
  2. Bear analyst argues AGAINST the trade, seeing the bull's last argument
After N rounds, the decision arbiter reads the full transcript and decides.

Set SHARK_DEBATE_ROUNDS=0 for the legacy single-call path (combined_analyst).
Set SHARK_DEBATE_ROUNDS=1+ for adversarial debate (default: 1).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from shark.config import get_settings

logger = logging.getLogger(__name__)

try:
    import anthropic as _anthropic_lib
except ImportError:
    _anthropic_lib = None

from shark.agents.trade_reviewer import get_recent_lessons


# ---------------------------------------------------------------------------
# System prompts — each analyst has a distinct persona
# ---------------------------------------------------------------------------

_BULL_SYSTEM = (
    "You are an experienced bullish equity analyst at a top hedge fund. "
    "Your job is to build a strong, evidence-based case for investing in the stock. "
    "Focus on: growth potential, competitive advantages, positive catalysts, "
    "and technical momentum. Directly counter the bear's arguments with data. "
    "Be conversational and engaging — debate, don't just list facts. "
    "Always return valid JSON."
)

_BEAR_SYSTEM = (
    "You are a skeptical short-seller and risk analyst. "
    "Your job is to stress-test the bull case and find every reason the trade could fail. "
    "Focus on: risks, competitive weaknesses, negative indicators, and macro threats. "
    "Directly counter the bull's arguments with data. "
    "Be conversational and engaging — debate, don't just list facts. "
    "Always return valid JSON."
)

_ARBITER_SYSTEM = (
    "You are the final decision-maker for a disciplined trading fund. "
    "You have just read a structured debate between a bull analyst and a bear analyst. "
    "Your job is to weigh the evidence objectively, determine which side had "
    "stronger arguments, and make a decisive GO/NO-GO call. "
    "Be bold when evidence is clear; use WAIT or NO_TRADE when it's not. "
    "Always return valid JSON."
)


# ---------------------------------------------------------------------------
# Bull round
# ---------------------------------------------------------------------------

def _run_bull_round(
    symbol: str,
    market_data: dict,
    perplexity_intel: dict,
    debate_history: str,
    bear_last_argument: str,
    round_num: int,
    total_rounds: int,
) -> dict[str, Any]:
    """Run one bull analyst round. Returns structured bull argument dict."""
    client = _anthropic_lib.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    counter_section = ""
    if bear_last_argument:
        counter_section = f"""
## Bear Analyst's Last Argument (COUNTER THIS)
{bear_last_argument}
"""

    history_section = ""
    if debate_history:
        history_section = f"""
## Debate History So Far
{debate_history}
"""

    prompt = f"""Round {round_num}/{total_rounds} — Build a compelling BULL case for {symbol}.

## Market Data
```json
{json.dumps(market_data, indent=2, default=str)}
```

## Research Intel
```json
{json.dumps(perplexity_intel, indent=2, default=str)}
```
{counter_section}{history_section}
Return ONLY this JSON:
{{
  "argument": "<3-5 sentence bull argument citing specific data, countering bear points if any>",
  "key_catalysts": ["<catalyst 1>", "<catalyst 2>"],
  "target_price": <float>,
  "confidence": <0.0-1.0>,
  "counter_to_bear": "<specific rebuttal to bear's strongest point, or empty if first round>"
}}"""

    try:
        response = client.messages.create(
            model=get_settings().claude_model,
            max_tokens=800,
            temperature=0.4,
            system=[{"type": "text", "text": _BULL_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()
        result = json.loads(raw)
        result.setdefault("argument", "")
        result.setdefault("confidence", 0.5)
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))
        return result
    except Exception as exc:
        logger.error("Bull round %d failed for %s: %s", round_num, symbol, exc)
        return {"argument": f"Bull analysis unavailable: {exc}", "confidence": 0.3,
                "key_catalysts": [], "target_price": 0.0, "counter_to_bear": ""}


# ---------------------------------------------------------------------------
# Bear round
# ---------------------------------------------------------------------------

def _run_bear_round(
    symbol: str,
    market_data: dict,
    perplexity_intel: dict,
    debate_history: str,
    bull_last_argument: str,
    round_num: int,
    total_rounds: int,
) -> dict[str, Any]:
    """Run one bear analyst round. Returns structured bear argument dict."""
    client = _anthropic_lib.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    counter_section = ""
    if bull_last_argument:
        counter_section = f"""
## Bull Analyst's Last Argument (COUNTER THIS)
{bull_last_argument}
"""

    history_section = ""
    if debate_history:
        history_section = f"""
## Debate History So Far
{debate_history}
"""

    prompt = f"""Round {round_num}/{total_rounds} — Build a compelling BEAR case against {symbol}.

## Market Data
```json
{json.dumps(market_data, indent=2, default=str)}
```

## Research Intel
```json
{json.dumps(perplexity_intel, indent=2, default=str)}
```
{counter_section}{history_section}
Return ONLY this JSON:
{{
  "argument": "<3-5 sentence bear argument citing specific risks and data, countering bull points>",
  "key_risks": ["<risk 1>", "<risk 2>"],
  "downside_target": <float>,
  "stop_recommended": <float>,
  "confidence": <0.0-1.0>,
  "counter_to_bull": "<specific rebuttal to bull's strongest point>"
}}"""

    try:
        response = client.messages.create(
            model=get_settings().claude_model,
            max_tokens=800,
            temperature=0.4,
            system=[{"type": "text", "text": _BEAR_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()
        result = json.loads(raw)
        result.setdefault("argument", "")
        result.setdefault("confidence", 0.5)
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))
        return result
    except Exception as exc:
        logger.error("Bear round %d failed for %s: %s", round_num, symbol, exc)
        return {"argument": f"Bear analysis unavailable: {exc}", "confidence": 0.3,
                "key_risks": [], "downside_target": 0.0, "stop_recommended": 0.0,
                "counter_to_bull": ""}


# ---------------------------------------------------------------------------
# Arbiter — reads debate transcript, makes final call
# ---------------------------------------------------------------------------

def _run_arbiter(
    symbol: str,
    market_data: dict,
    risk_check: dict,
    debate_transcript: str,
    bull_final: dict,
    bear_final: dict,
    lessons: list[str],
) -> dict[str, Any]:
    """Final decision after the debate. Returns trade decision dict."""
    client = _anthropic_lib.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    lessons_block = ""
    if lessons:
        lessons_block = "\n## Lessons from Past Trades\n" + "\n".join(
            f"- {lesson}" for lesson in lessons
        ) + "\n"

    prompt = f"""Make a final trading decision for {symbol} after reading the bull-bear debate.

## Full Debate Transcript
{debate_transcript}

## Risk Manager Assessment
```json
{json.dumps(risk_check, indent=2, default=str)}
```

## Current Market Data
```json
{json.dumps(market_data, indent=2, default=str)}
```
{lessons_block}
Return ONLY this JSON:
{{
  "decision": "<BUY | NO_TRADE | WAIT>",
  "symbol": "{symbol}",
  "confidence": <0.0-1.0>,
  "position_size_pct": <float 0-20>,
  "entry_price": <float>,
  "stop_loss": <float>,
  "target_price": <float>,
  "risk_reward_ratio": <float>,
  "reasoning": "<2-3 sentence rationale referencing specific debate points>",
  "thesis_summary": "<one-line summary>",
  "winning_side": "<BULL or BEAR — which side had stronger arguments>"
}}

Rules:
- Only BUY if confidence >= 0.70 AND risk_reward_ratio >= 2.0
- Reference specific debate points in your reasoning
- WAIT if setup is promising but timing is wrong
- NO_TRADE if risks outweigh the opportunity"""

    try:
        response = client.messages.create(
            model=get_settings().claude_model,
            max_tokens=1000,
            temperature=0.2,
            system=[{"type": "text", "text": _ARBITER_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()
        result = json.loads(raw)

        result.setdefault("decision", "NO_TRADE")
        result.setdefault("symbol", symbol)
        result.setdefault("confidence", 0.0)
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

        # Enforce confidence gate
        if result["decision"] == "BUY" and result["confidence"] < 0.70:
            result["decision"] = "NO_TRADE"
            result["reasoning"] = (
                f"Downgraded: confidence {result['confidence']:.2f} < 0.70 threshold. "
                + result.get("reasoning", "")
            )

        return result

    except Exception as exc:
        logger.error("Arbiter failed for %s: %s", symbol, exc)
        return {
            "decision": "NO_TRADE", "symbol": symbol, "confidence": 0.0,
            "position_size_pct": 0.0, "entry_price": 0.0, "stop_loss": 0.0,
            "target_price": 0.0, "risk_reward_ratio": 0.0,
            "reasoning": f"Arbiter error: {exc}", "thesis_summary": f"NO_TRADE — error",
        }


# ---------------------------------------------------------------------------
# Orchestrator — the main entry point
# ---------------------------------------------------------------------------

def run_debate(
    symbol: str,
    market_data: dict[str, Any],
    perplexity_intel: dict[str, Any],
    risk_check: dict[str, Any],
    rounds: int = 1,
) -> dict[str, Any]:
    """
    Run a full adversarial bull-bear debate for one symbol.

    Args:
        symbol: Ticker symbol.
        market_data: Compressed market data dict.
        perplexity_intel: Research intel dict.
        risk_check: Output from guardrails / risk manager.
        rounds: Number of debate rounds (each round = 1 bull + 1 bear turn).

    Returns:
        Dict with keys: bull, bear, decision, debate_transcript, combined.
        Same shape as combined_analyst output for backward compatibility.
    """
    if not risk_check.get("approved", False):
        violations = risk_check.get("violations", ["risk check failed"])
        return _no_debate_result(symbol, f"Risk check failed: {'; '.join(violations)}")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or _anthropic_lib is None:
        logger.warning("No API key — debate requires Claude. Falling back to single-call.")
        from shark.agents.combined_analyst import _rule_based_analyze
        return _rule_based_analyze(symbol, market_data, perplexity_intel, risk_check)

    debate_transcript = ""
    bull_last = ""
    bear_last = ""
    bull_result = {}
    bear_result = {}
    lessons = get_recent_lessons(n=5)

    for r in range(1, rounds + 1):
        logger.info("Debate %s round %d/%d — BULL turn", symbol, r, rounds)
        bull_result = _run_bull_round(
            symbol, market_data, perplexity_intel,
            debate_transcript, bear_last, r, rounds,
        )
        bull_arg = bull_result.get("argument", "")
        debate_transcript += f"\n### Round {r} — Bull Analyst\n{bull_arg}\n"
        bull_last = bull_arg

        logger.info("Debate %s round %d/%d — BEAR turn", symbol, r, rounds)
        bear_result = _run_bear_round(
            symbol, market_data, perplexity_intel,
            debate_transcript, bull_last, r, rounds,
        )
        bear_arg = bear_result.get("argument", "")
        debate_transcript += f"\n### Round {r} — Bear Analyst\n{bear_arg}\n"
        bear_last = bear_arg

    logger.info("Debate %s — ARBITER deciding", symbol)
    decision = _run_arbiter(
        symbol, market_data, risk_check,
        debate_transcript, bull_result, bear_result, lessons,
    )

    # Convert to backward-compatible format
    bull_thesis = {
        "symbol": symbol,
        "thesis": bull_result.get("argument", ""),
        "catalysts": bull_result.get("key_catalysts", []),
        "target_price": bull_result.get("target_price", 0.0),
        "entry_zone": {"low": 0.0, "high": 0.0},
        "timeframe_days": 5,
        "confidence": bull_result.get("confidence", 0.0),
        "supporting_data": bull_result.get("counter_to_bear", ""),
    }

    bear_thesis = {
        "symbol": symbol,
        "counter_thesis": bear_result.get("argument", ""),
        "risks": bear_result.get("key_risks", []),
        "downside_target": bear_result.get("downside_target", 0.0),
        "stop_recommended": bear_result.get("stop_recommended", 0.0),
        "invalidation_signal": bear_result.get("counter_to_bull", ""),
        "confidence": bear_result.get("confidence", 0.0),
    }

    logger.info(
        "Debate result %s: decision=%s confidence=%.2f winning_side=%s",
        symbol, decision.get("decision"), decision.get("confidence", 0),
        decision.get("winning_side", "N/A"),
    )

    return {
        "bull": bull_thesis,
        "bear": bear_thesis,
        "decision": decision,
        "debate_transcript": debate_transcript,
        "debate_rounds": rounds,
        "combined": True,
    }


def _no_debate_result(symbol: str, reason: str) -> dict[str, Any]:
    """Return a NO_TRADE result without running the debate."""
    base_bull = {
        "symbol": symbol, "thesis": "", "catalysts": [], "target_price": 0.0,
        "entry_zone": {"low": 0.0, "high": 0.0}, "timeframe_days": 0,
        "confidence": 0.0, "supporting_data": "", "error": reason,
    }
    base_bear = {
        "symbol": symbol, "counter_thesis": "", "risks": [],
        "downside_target": 0.0, "stop_recommended": 0.0,
        "invalidation_signal": "", "confidence": 0.0, "error": reason,
    }
    base_decision = {
        "decision": "NO_TRADE", "symbol": symbol, "confidence": 0.0,
        "position_size_pct": 0.0, "entry_price": 0.0, "stop_loss": 0.0,
        "target_price": 0.0, "risk_reward_ratio": 0.0,
        "reasoning": reason, "thesis_summary": f"NO_TRADE — {reason}",
    }
    return {
        "bull": base_bull, "bear": base_bear, "decision": base_decision,
        "debate_transcript": "", "debate_rounds": 0, "combined": True,
    }
