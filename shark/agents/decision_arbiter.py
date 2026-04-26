"""
Decision Arbiter — final GO/NO-GO trading decision using Claude.

Synthesizes bull thesis, bear thesis, and risk manager output to make a
disciplined, high-conviction final call. Enforces confidence and risk thresholds.
"""

import json
import os
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.70
_MIN_RISK_REWARD = 2.0


def make_decision(
    bull_thesis: dict[str, Any],
    bear_thesis: dict[str, Any],
    risk_check: dict[str, Any],
    market_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Make a final BUY / NO_TRADE / WAIT decision by arbitrating between analysts.

    If risk_check["approved"] is False, returns NO_TRADE immediately without
    calling the Claude API. After the API call, any result with confidence < 0.70
    is also downgraded to NO_TRADE.

    Args:
        bull_thesis: Output from analyst_bull.generate_bull_thesis().
        bear_thesis: Output from analyst_bear.generate_bear_thesis().
        risk_check: Output from risk_manager.check_risk().
        market_data: Current OHLCV and technical data for the symbol.

    Returns:
        Dict with keys: decision, symbol, confidence, position_size_pct,
        entry_price, stop_loss, target_price, risk_reward_ratio, reasoning,
        thesis_summary.
    """
    symbol = bull_thesis.get("symbol", bear_thesis.get("symbol", "UNKNOWN"))

    # Hard gate: if risk manager rejected, no API call needed
    if not risk_check.get("approved", False):
        violations = risk_check.get("violations", ["Risk check failed"])
        logger.warning(
            "Decision for %s forced to NO_TRADE — risk check not approved: %s",
            symbol,
            violations,
        )
        return {
            "decision": "NO_TRADE",
            "symbol": symbol,
            "confidence": 0.0,
            "position_size_pct": 0.0,
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "target_price": 0.0,
            "risk_reward_ratio": 0.0,
            "reasoning": f"Risk manager rejected trade. Violations: {'; '.join(violations)}",
            "thesis_summary": f"NO_TRADE — {symbol} failed risk checks.",
        }

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    system_prompt = (
        "You are the final decision-maker for a disciplined trading fund. "
        "You receive analysis from a bull analyst, a bear analyst, and a risk manager. "
        "Your job is to weigh the evidence objectively and make a final GO/NO-GO decision. "
        "Be decisive. Only approve trades with high conviction and clean risk profiles. "
        "Always return valid JSON."
    )

    user_prompt = f"""Make a final trading decision for {symbol} based on the following inputs:

## Bull Analyst Thesis
```json
{json.dumps(bull_thesis, indent=2, default=str)}
```

## Bear Analyst Counter-Thesis
```json
{json.dumps(bear_thesis, indent=2, default=str)}
```

## Risk Manager Assessment
```json
{json.dumps(risk_check, indent=2, default=str)}
```

## Current Market Data
```json
{json.dumps(market_data, indent=2, default=str)}
```

Return ONLY a valid JSON object with this exact structure:
{{
  "decision": "<BUY | NO_TRADE | WAIT>",
  "symbol": "{symbol}",
  "confidence": <float 0.0-1.0 — only choose BUY if >= 0.70>,
  "position_size_pct": <float — recommended % of portfolio to allocate, respect risk limits>,
  "entry_price": <float — specific entry price>,
  "stop_loss": <float — specific stop loss price>,
  "target_price": <float — specific price target>,
  "risk_reward_ratio": <float — must be >= 2.0 for BUY>,
  "reasoning": "<2-3 sentence explanation of the decision weighing bull vs bear evidence>",
  "thesis_summary": "<1 sentence suitable for signals subscribers>"
}}

Rules:
- Only set decision to "BUY" if confidence >= 0.70 AND risk_reward_ratio >= 2.0
- Use "WAIT" if the setup is promising but timing is not right
- Use "NO_TRADE" if the risks outweigh the opportunity
- All price levels must be realistic given the market data
- Do not include any text outside the JSON object."""

    try:
        response = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=800,
            temperature=0.2,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            lines = raw_text.splitlines()
            raw_text = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        result = json.loads(raw_text)

        # Ensure required fields
        result.setdefault("decision", "NO_TRADE")
        result.setdefault("symbol", symbol)
        result.setdefault("confidence", 0.0)
        result.setdefault("position_size_pct", 0.0)
        result.setdefault("entry_price", 0.0)
        result.setdefault("stop_loss", 0.0)
        result.setdefault("target_price", 0.0)
        result.setdefault("risk_reward_ratio", 0.0)
        result.setdefault("reasoning", "")
        result.setdefault("thesis_summary", "")

        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

        # Enforce confidence threshold
        if result["confidence"] < _MIN_CONFIDENCE and result["decision"] == "BUY":
            logger.info(
                "Decision for %s downgraded from BUY to NO_TRADE — confidence %.2f < %.2f",
                symbol,
                result["confidence"],
                _MIN_CONFIDENCE,
            )
            result["decision"] = "NO_TRADE"
            result["reasoning"] += (
                f" Confidence {result['confidence']:.0%} below required threshold of "
                f"{_MIN_CONFIDENCE:.0%}."
            )

        logger.info(
            "Decision for %s: %s (confidence=%.2f, R:R=%.1f)",
            symbol,
            result["decision"],
            result["confidence"],
            result.get("risk_reward_ratio", 0.0),
        )

        return result

    except json.JSONDecodeError as exc:
        logger.error("Decision arbiter JSON parse error for %s: %s", symbol, exc)
        return {
            "decision": "NO_TRADE",
            "symbol": symbol,
            "confidence": 0.0,
            "position_size_pct": 0.0,
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "target_price": 0.0,
            "risk_reward_ratio": 0.0,
            "reasoning": f"Internal error: JSON parse failed — {exc}",
            "thesis_summary": f"NO_TRADE — {symbol} decision failed due to parse error.",
            "error": str(exc),
        }

    except anthropic.APIError as exc:
        logger.error("Anthropic API error in decision arbiter for %s: %s", symbol, exc)
        return {
            "decision": "NO_TRADE",
            "symbol": symbol,
            "confidence": 0.0,
            "position_size_pct": 0.0,
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "target_price": 0.0,
            "risk_reward_ratio": 0.0,
            "reasoning": f"API error during decision: {exc}",
            "thesis_summary": f"NO_TRADE — {symbol} decision unavailable due to API error.",
            "error": str(exc),
        }

    except Exception as exc:
        logger.error("Unexpected error in decision arbiter for %s: %s", symbol, exc)
        return {
            "decision": "NO_TRADE",
            "symbol": symbol,
            "confidence": 0.0,
            "position_size_pct": 0.0,
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "target_price": 0.0,
            "risk_reward_ratio": 0.0,
            "reasoning": f"Unexpected error: {exc}",
            "thesis_summary": f"NO_TRADE — {symbol} decision failed.",
            "error": str(exc),
        }
