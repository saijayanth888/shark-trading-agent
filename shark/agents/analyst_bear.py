"""
Bear Analyst Agent — stress-tests bull theses and generates bearish counter-analysis.
"""

import json
import os
import logging
from typing import Any

import anthropic
from shark.config import get_settings

logger = logging.getLogger(__name__)


def generate_bear_thesis(
    symbol: str,
    market_data: dict[str, Any],
    perplexity_intel: dict[str, Any],
) -> dict[str, Any]:
    """
    Generate a bearish counter-thesis and risk assessment for the given symbol.

    Uses Claude with a cached system prompt to challenge bull assumptions and
    surface every meaningful risk that could cause the trade to fail.

    Args:
        symbol: Ticker symbol (e.g. "AAPL").
        market_data: Dict of OHLCV, technicals, and fundamentals for the symbol.
        perplexity_intel: Dict of recent news, sentiment, and analyst opinions.

    Returns:
        A dict with keys: symbol, counter_thesis, risks, downside_target,
        stop_recommended, invalidation_signal, confidence.
        On failure: includes an "error" key and confidence=0.0.
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    system_prompt = (
        "You are a skeptical short-seller and risk analyst. "
        "Your job is to stress-test bull theses and find every reason a trade could fail. "
        "Be ruthless. Always return valid JSON."
    )

    user_prompt = f"""Analyze the following data for {symbol} and generate a bearish counter-thesis that stress-tests any bullish view.

## Market Data for {symbol}
```json
{json.dumps(market_data, indent=2, default=str)}
```

## Recent Intelligence (News, Sentiment, Analyst Views)
```json
{json.dumps(perplexity_intel, indent=2, default=str)}
```

Return ONLY a valid JSON object with this exact structure:
{{
  "symbol": "{symbol}",
  "counter_thesis": "<2-3 sentence bearish counter-thesis citing specific data and risks>",
  "risks": ["<specific risk 1>", "<specific risk 2>", "<specific risk 3>", "<specific risk 4>"],
  "downside_target": <float — realistic downside price target>,
  "stop_recommended": <float — price level that would recommended as stop loss>,
  "invalidation_signal": "<what specific price action or event would invalidate the bear case>",
  "confidence": <float between 0.0 and 1.0 — confidence in the bearish view>
}}

Be specific about price levels based on the market data provided. Do not include any text outside the JSON object."""

    try:
        cfg = get_settings()
        response = client.messages.create(
            model=cfg.claude_model,
            max_tokens=1000,
            temperature=0.3,
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

        thesis = json.loads(raw_text)

        # Ensure required keys are present and typed correctly
        thesis.setdefault("symbol", symbol)
        thesis.setdefault("counter_thesis", "")
        thesis.setdefault("risks", [])
        thesis.setdefault("downside_target", 0.0)
        thesis.setdefault("stop_recommended", 0.0)
        thesis.setdefault("invalidation_signal", "")
        thesis.setdefault("confidence", 0.0)

        thesis["confidence"] = max(0.0, min(1.0, float(thesis["confidence"])))

        return thesis

    except json.JSONDecodeError as exc:
        logger.error("Bear analyst JSON parse error for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "counter_thesis": "",
            "risks": [],
            "downside_target": 0.0,
            "stop_recommended": 0.0,
            "invalidation_signal": "",
            "confidence": 0.0,
            "error": f"JSON parse error: {exc}",
        }

    except anthropic.APIError as exc:
        logger.error("Anthropic API error in bear analyst for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "counter_thesis": "",
            "risks": [],
            "downside_target": 0.0,
            "stop_recommended": 0.0,
            "invalidation_signal": "",
            "confidence": 0.0,
            "error": f"API error: {exc}",
        }

    except Exception as exc:
        logger.error("Unexpected error in bear analyst for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "counter_thesis": "",
            "risks": [],
            "downside_target": 0.0,
            "stop_recommended": 0.0,
            "invalidation_signal": "",
            "confidence": 0.0,
            "error": f"Unexpected error: {exc}",
        }
