"""
LLM Risk Debate — 3-way risk discussion after deterministic guardrails pass.

Inspired by TradingAgents' aggressive/conservative/neutral risk debate.
This runs AFTER the deterministic guardrails (position sizer, regime gates, etc.)
and can veto or adjust a trade that guardrails approved but that has qualitative
risks the rules can't catch (e.g., CEO departure, regulatory overhang).

Enable via SHARK_LLM_RISK_REVIEW=true (default: false).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    import anthropic as _anthropic_lib
except ImportError:
    _anthropic_lib = None


# ---------------------------------------------------------------------------
# Perspective prompts
# ---------------------------------------------------------------------------

_AGGRESSIVE_SYSTEM = (
    "You are an aggressive risk analyst who champions high-reward opportunities. "
    "You believe calculated risk-taking is essential for alpha generation. "
    "Focus on upside potential, market momentum, and why caution may cause missed opportunities. "
    "Counter conservative/neutral concerns with data-driven optimism. "
    "Always return valid JSON."
)

_CONSERVATIVE_SYSTEM = (
    "You are a conservative risk analyst who prioritizes capital preservation. "
    "You believe avoiding losses is more important than capturing gains. "
    "Focus on downside risks, position sizing dangers, macro headwinds, and tail risks. "
    "Challenge aggressive assumptions with worst-case scenarios. "
    "Always return valid JSON."
)

_NEUTRAL_SYSTEM = (
    "You are a balanced risk analyst who seeks optimal risk-adjusted returns. "
    "You weigh both aggressive and conservative viewpoints objectively. "
    "Focus on finding the right position size, stop placement, and timing. "
    "Synthesize the best points from both sides into a pragmatic recommendation. "
    "Always return valid JSON."
)

_JUDGE_SYSTEM = (
    "You are the portfolio risk committee chair. You've read a 3-way risk debate "
    "(aggressive, conservative, neutral) about a proposed trade. "
    "Synthesize their arguments into a final risk-adjusted recommendation. "
    "Be decisive. If any perspective raised a legitimate dealbreaker, veto the trade. "
    "Always return valid JSON."
)


def _run_perspective(
    perspective: str,
    system_prompt: str,
    symbol: str,
    trade_decision: dict,
    market_data: dict,
    debate_history: str,
    other_arguments: dict[str, str],
    round_num: int,
) -> dict[str, Any]:
    """Run one risk perspective. Returns structured assessment."""
    client = _anthropic_lib.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    others_section = ""
    for name, arg in other_arguments.items():
        if arg:
            others_section += f"\n## {name} Analyst's Argument\n{arg}\n"

    history_section = ""
    if debate_history:
        history_section = f"\n## Risk Debate History\n{debate_history}\n"

    prompt = f"""Evaluate this proposed {trade_decision.get('decision', 'BUY')} trade for {symbol} from your {perspective} risk perspective.

## Proposed Trade
```json
{json.dumps(trade_decision, indent=2, default=str)}
```

## Market Context
```json
{json.dumps(market_data, indent=2, default=str)}
```
{others_section}{history_section}
Return ONLY this JSON:
{{
  "assessment": "<2-4 sentence risk assessment from {perspective} perspective>",
  "recommended_action": "<BUY | NO_TRADE | WAIT>",
  "position_size_adjustment": <float 0.0-2.0 — multiplier to apply to position size>,
  "confidence_adjustment": <float -0.3 to +0.3 — adjustment to confidence score>,
  "key_concern": "<single most important risk factor from your perspective>",
  "counter_arguments": "<specific rebuttals to other perspectives>"
}}"""

    try:
        response = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=600,
            temperature=0.3,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()
        result = json.loads(raw)
        result.setdefault("assessment", "")
        result.setdefault("recommended_action", "NO_TRADE")
        result.setdefault("position_size_adjustment", 1.0)
        result.setdefault("confidence_adjustment", 0.0)
        result["position_size_adjustment"] = max(0.0, min(2.0, float(result["position_size_adjustment"])))
        result["confidence_adjustment"] = max(-0.3, min(0.3, float(result["confidence_adjustment"])))
        return result
    except Exception as exc:
        logger.error("Risk %s perspective failed for %s: %s", perspective, symbol, exc)
        return {
            "assessment": f"{perspective} analysis unavailable: {exc}",
            "recommended_action": "NO_TRADE",
            "position_size_adjustment": 0.8,
            "confidence_adjustment": -0.1,
            "key_concern": "Risk analysis unavailable",
        }


def _run_risk_judge(
    symbol: str,
    trade_decision: dict,
    perspectives: dict[str, dict],
    debate_transcript: str,
) -> dict[str, Any]:
    """Synthesize the 3-way risk debate into a final recommendation."""
    client = _anthropic_lib.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = f"""Synthesize this 3-way risk debate for {symbol} into a final risk recommendation.

## Original Trade Proposal
```json
{json.dumps(trade_decision, indent=2, default=str)}
```

## Risk Debate Transcript
{debate_transcript}

## Summary of Perspectives
- Aggressive: {perspectives.get('aggressive', {}).get('assessment', 'N/A')}
- Conservative: {perspectives.get('conservative', {}).get('assessment', 'N/A')}
- Neutral: {perspectives.get('neutral', {}).get('assessment', 'N/A')}

Return ONLY this JSON:
{{
  "final_action": "<BUY | NO_TRADE | WAIT>",
  "final_confidence": <float 0.0-1.0>,
  "position_size_multiplier": <float 0.0-2.0>,
  "summary": "<2-sentence synthesis of the risk debate>",
  "vetoed": <true | false — true if any perspective raised a dealbreaker>
}}"""

    try:
        response = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=600,
            temperature=0.2,
            system=[{"type": "text", "text": _JUDGE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()
        result = json.loads(raw)
        result.setdefault("final_action", "NO_TRADE")
        result.setdefault("final_confidence", 0.0)
        result.setdefault("position_size_multiplier", 1.0)
        result.setdefault("vetoed", False)
        return result
    except Exception as exc:
        logger.error("Risk judge failed for %s: %s", symbol, exc)
        return {
            "final_action": "NO_TRADE", "final_confidence": 0.0,
            "position_size_multiplier": 0.8, "summary": f"Risk judge error: {exc}",
            "vetoed": True,
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_risk_debate(
    symbol: str,
    trade_decision: dict[str, Any],
    market_data: dict[str, Any],
    rounds: int = 1,
) -> dict[str, Any]:
    """
    Run a 3-way risk debate (aggressive/conservative/neutral) on a proposed trade.

    Args:
        symbol: Ticker symbol.
        trade_decision: The trade decision from the arbiter/debate.
        market_data: Current market data for context.
        rounds: Number of debate rounds (each round = all 3 perspectives speak).

    Returns:
        Dict with:
            approved (bool): Whether the risk debate approves the trade
            adjusted_decision (dict): Modified trade decision
            confidence_delta (float): Net confidence adjustment
            position_size_mult (float): Final position size multiplier
            debate_summary (str): Summary of the risk debate
            perspectives (dict): Individual perspective results
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or _anthropic_lib is None:
        logger.info("LLM risk review skipped — no API key")
        return {"approved": True, "adjusted_decision": trade_decision,
                "confidence_delta": 0.0, "position_size_mult": 1.0,
                "debate_summary": "Skipped — no API key", "perspectives": {}}

    debate_transcript = ""
    perspectives = {"aggressive": {}, "conservative": {}, "neutral": {}}
    perspective_order = [
        ("aggressive", _AGGRESSIVE_SYSTEM),
        ("conservative", _CONSERVATIVE_SYSTEM),
        ("neutral", _NEUTRAL_SYSTEM),
    ]

    for r in range(1, rounds + 1):
        for name, system in perspective_order:
            others = {
                k: v.get("assessment", "")
                for k, v in perspectives.items()
                if k != name and v
            }

            logger.info("Risk debate %s round %d/%d — %s", symbol, r, rounds, name.upper())
            result = _run_perspective(
                perspective=name,
                system_prompt=system,
                symbol=symbol,
                trade_decision=trade_decision,
                market_data=market_data,
                debate_history=debate_transcript,
                other_arguments=others,
                round_num=r,
            )
            perspectives[name] = result
            assessment = result.get("assessment", "")
            debate_transcript += f"\n### Round {r} — {name.capitalize()} Analyst\n{assessment}\n"

    # Judge synthesizes
    logger.info("Risk debate %s — JUDGE synthesizing", symbol)
    judge_result = _run_risk_judge(symbol, trade_decision, perspectives, debate_transcript)

    # Build adjusted decision
    adjusted = dict(trade_decision)
    vetoed = judge_result.get("vetoed", False)
    if vetoed:
        adjusted["decision"] = "NO_TRADE"
        adjusted["reasoning"] = (
            f"VETOED by risk debate: {judge_result.get('summary', '')}. "
            + adjusted.get("reasoning", "")
        )
    else:
        adjusted["confidence"] = max(0.0, min(1.0, judge_result.get("final_confidence",
                                                                      trade_decision.get("confidence", 0))))

    # Compute net confidence delta
    conf_delta = sum(
        p.get("confidence_adjustment", 0.0) for p in perspectives.values()
    ) / max(len(perspectives), 1)

    size_mult = judge_result.get("position_size_multiplier", 1.0)

    logger.info(
        "Risk debate %s: approved=%s conf_delta=%.2f size_mult=%.2f",
        symbol, not vetoed, conf_delta, size_mult,
    )

    return {
        "approved": not vetoed,
        "adjusted_decision": adjusted,
        "confidence_delta": round(conf_delta, 3),
        "position_size_mult": round(size_mult, 2),
        "debate_summary": judge_result.get("summary", ""),
        "debate_transcript": debate_transcript,
        "perspectives": perspectives,
    }
