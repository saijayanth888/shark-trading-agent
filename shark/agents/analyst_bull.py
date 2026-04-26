"""
Bull Analyst Agent — generates a bullish thesis for a given symbol using Claude.
"""

import json
import os
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)


def generate_bull_thesis(
    symbol: str,
    market_data: dict[str, Any],
    perplexity_intel: dict[str, Any],
) -> dict[str, Any]:
    """
    Generate a bullish investment thesis for the given symbol.

    Uses Claude with a cached system prompt to produce a structured JSON
    bull thesis including target price, entry zone, catalysts, and confidence.

    Args:
        symbol: Ticker symbol (e.g. "AAPL").
        market_data: Dict of OHLCV, technicals, and fundamentals for the symbol.
        perplexity_intel: Dict of recent news, sentiment, and analyst opinions.

    Returns:
        A dict with keys: symbol, thesis, catalysts, target_price, entry_zone,
        timeframe_days, confidence, supporting_data.
        On failure: includes an "error" key and confidence=0.0.
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    system_prompt = (
        "You are an experienced bullish equity analyst at a top hedge fund. "
        "Your job is to find compelling long opportunities and build conviction. "
        "Be specific, cite data, and quantify your thesis. Always return valid JSON."
    )

    user_prompt = f"""Analyze the following data for {symbol} and generate a bullish investment thesis.

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
  "thesis": "<2-3 sentence bull thesis with specific data points>",
  "catalysts": ["<catalyst 1>", "<catalyst 2>", "<catalyst 3>"],
  "target_price": <float>,
  "entry_zone": {{"low": <float>, "high": <float>}},
  "timeframe_days": <int>,
  "confidence": <float between 0.0 and 1.0>,
  "supporting_data": "<key data points that support the thesis>"
}}

Be specific about price levels based on the market data provided. Do not include any text outside the JSON object."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
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
        thesis.setdefault("thesis", "")
        thesis.setdefault("catalysts", [])
        thesis.setdefault("target_price", 0.0)
        thesis.setdefault("entry_zone", {"low": 0.0, "high": 0.0})
        thesis.setdefault("timeframe_days", 0)
        thesis.setdefault("confidence", 0.0)
        thesis.setdefault("supporting_data", "")

        thesis["confidence"] = max(0.0, min(1.0, float(thesis["confidence"])))

        return thesis

    except json.JSONDecodeError as exc:
        logger.error("Bull analyst JSON parse error for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "thesis": "",
            "catalysts": [],
            "target_price": 0.0,
            "entry_zone": {"low": 0.0, "high": 0.0},
            "timeframe_days": 0,
            "confidence": 0.0,
            "supporting_data": "",
            "error": f"JSON parse error: {exc}",
        }

    except anthropic.APIError as exc:
        logger.error("Anthropic API error in bull analyst for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "thesis": "",
            "catalysts": [],
            "target_price": 0.0,
            "entry_zone": {"low": 0.0, "high": 0.0},
            "timeframe_days": 0,
            "confidence": 0.0,
            "supporting_data": "",
            "error": f"API error: {exc}",
        }

    except Exception as exc:
        logger.error("Unexpected error in bull analyst for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "thesis": "",
            "catalysts": [],
            "target_price": 0.0,
            "entry_zone": {"low": 0.0, "high": 0.0},
            "timeframe_days": 0,
            "confidence": 0.0,
            "supporting_data": "",
            "error": f"Unexpected error: {exc}",
        }
