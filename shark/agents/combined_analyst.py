"""
Combined analyst — merges bull thesis, bear thesis, and final decision into ONE Claude call.

Replaces the three-call chain (analyst_bull → analyst_bear → decision_arbiter) with a
single structured call. Reduces token usage by ~78% per symbol.

Context compression:
  - Only last 5 OHLCV bars passed (not 60 days)
  - Only key technical indicators (RSI, MACD signal, BB width, volume_ratio)
  - max_tokens capped at 1200
"""

import json
import logging
import os
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a disciplined trading fund's analysis team. "
    "Given market data and research intel for a stock, you produce: "
    "(1) a concise bull thesis, (2) a concise bear counter-thesis, "
    "(3) a final BUY / NO_TRADE / WAIT decision. "
    "Be specific and data-driven. Only approve trades with conviction >= 0.70 and "
    "risk/reward >= 2.0. Always return valid JSON with no text outside the JSON object."
)


def _compress_market_data(technicals: dict[str, Any], bars: list[dict]) -> dict[str, Any]:
    """Return a compact market snapshot — last 5 candles + key indicators only."""
    last5 = bars[-5:] if len(bars) >= 5 else bars
    compact_bars = [
        {
            "date": str(b.get("t", b.get("date", ""))),
            "o": round(float(b.get("o", b.get("open", 0))), 2),
            "h": round(float(b.get("h", b.get("high", 0))), 2),
            "l": round(float(b.get("l", b.get("low", 0))), 2),
            "c": round(float(b.get("c", b.get("close", 0))), 2),
            "v": int(b.get("v", b.get("volume", 0))),
        }
        for b in last5
    ]

    return {
        "current_price": round(float(technicals.get("current_price", 0)), 2),
        "rsi_14": round(float(technicals.get("rsi", technicals.get("rsi_14", 50))), 1),
        "macd_signal": round(float(technicals.get("macd_signal", 0)), 4),
        "macd_histogram": round(float(technicals.get("macd_histogram", 0)), 4),
        "bb_upper": round(float(technicals.get("bb_upper", 0)), 2),
        "bb_lower": round(float(technicals.get("bb_lower", 0)), 2),
        "volume_ratio": round(float(technicals.get("volume_ratio", 1.0)), 2),
        "sma_20": round(float(technicals.get("sma_20", 0)), 2),
        "sma_50": round(float(technicals.get("sma_50", 0)), 2),
        "atr": round(float(technicals.get("atr", 0)), 2),
        "last_5_candles": compact_bars,
    }


def analyze_symbol(
    symbol: str,
    technicals: dict[str, Any],
    bars: list[dict],
    perplexity_intel: dict[str, Any],
    risk_check: dict[str, Any],
) -> dict[str, Any]:
    """
    Run bull + bear + decision analysis for one symbol in a single API call.

    Args:
        symbol: Ticker (e.g. "NVDA")
        technicals: Output of compute_indicators()
        bars: Raw OHLCV bars list (any length — internally truncated to last 5)
        perplexity_intel: Output of fetch_market_intel()[symbol]
        risk_check: Output of Guardrails.run_all() — must be pre-computed

    Returns:
        Dict with keys: bull, bear, decision (each a sub-dict), plus
        "combined" flag and "error" if something went wrong.
    """
    if not risk_check.get("approved", False):
        violations = risk_check.get("violations", ["risk check failed"])
        return _no_trade_result(symbol, f"Risk check failed: {'; '.join(violations)}")

    compact_data = _compress_market_data(technicals, bars)

    user_prompt = f"""Analyze {symbol} and return a single JSON object with bull_thesis, bear_thesis, and decision sections.

## Compressed Market Data
```json
{json.dumps(compact_data, indent=2)}
```

## Research Intel
```json
{json.dumps(perplexity_intel, indent=2, default=str)}
```

## Risk Manager Output
```json
{json.dumps(risk_check, indent=2, default=str)}
```

Return ONLY this JSON (no text outside it):
{{
  "bull_thesis": {{
    "symbol": "{symbol}",
    "thesis": "<2-sentence bull case citing specific data>",
    "catalysts": ["<catalyst 1>", "<catalyst 2>"],
    "target_price": <float>,
    "entry_zone": {{"low": <float>, "high": <float>}},
    "timeframe_days": <int>,
    "confidence": <0.0-1.0>,
    "supporting_data": "<key supporting facts>"
  }},
  "bear_thesis": {{
    "symbol": "{symbol}",
    "counter_thesis": "<2-sentence bear case>",
    "risks": ["<risk 1>", "<risk 2>", "<risk 3>"],
    "downside_target": <float>,
    "stop_recommended": <float>,
    "invalidation_signal": "<what invalidates bear case>",
    "confidence": <0.0-1.0>
  }},
  "decision": {{
    "decision": "<BUY or NO_TRADE or WAIT>",
    "symbol": "{symbol}",
    "confidence": <0.0-1.0>,
    "position_size_pct": <float 0-20>,
    "entry_price": <float>,
    "stop_loss": <float>,
    "target_price": <float>,
    "risk_reward_ratio": <float>,
    "reasoning": "<1-2 sentence rationale>",
    "thesis_summary": "<one-line summary>"
  }}
}}

Rules: Only set decision=BUY if confidence >= 0.70 AND risk_reward_ratio >= 2.0."""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    try:
        response = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=1200,
            temperature=0.2,
            system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(l for l in lines if not l.startswith("```")).strip()

        result = json.loads(raw)

        bull = result.get("bull_thesis", {})
        bear = result.get("bear_thesis", {})
        decision = result.get("decision", {})

        # Normalize
        bull.setdefault("symbol", symbol)
        bull.setdefault("confidence", 0.0)
        bull["confidence"] = max(0.0, min(1.0, float(bull["confidence"])))

        bear.setdefault("symbol", symbol)
        bear.setdefault("confidence", 0.0)
        bear["confidence"] = max(0.0, min(1.0, float(bear["confidence"])))

        decision.setdefault("symbol", symbol)
        decision.setdefault("decision", "NO_TRADE")
        decision.setdefault("confidence", 0.0)
        decision["confidence"] = max(0.0, min(1.0, float(decision["confidence"])))

        # Enforce confidence gate
        if decision.get("decision") == "BUY" and decision["confidence"] < 0.70:
            decision["decision"] = "NO_TRADE"
            decision["reasoning"] = (
                f"Downgraded: confidence {decision['confidence']:.2f} < 0.70 threshold. "
                + decision.get("reasoning", "")
            )

        logger.info(
            "Combined analysis %s: decision=%s confidence=%.2f rr=%.1f",
            symbol,
            decision["decision"],
            decision["confidence"],
            decision.get("risk_reward_ratio", 0),
        )

        return {"bull": bull, "bear": bear, "decision": decision, "combined": True}

    except json.JSONDecodeError as exc:
        logger.error("Combined analyst JSON parse error for %s: %s", symbol, exc)
        return _no_trade_result(symbol, f"JSON parse error: {exc}")
    except anthropic.APIError as exc:
        logger.error("API error in combined analyst for %s: %s", symbol, exc)
        return _no_trade_result(symbol, f"API error: {exc}")
    except Exception as exc:
        logger.error("Unexpected error in combined analyst for %s: %s", symbol, exc)
        return _no_trade_result(symbol, f"Unexpected error: {exc}")


def _no_trade_result(symbol: str, reason: str) -> dict[str, Any]:
    base = {
        "symbol": symbol, "confidence": 0.0, "thesis": "", "catalysts": [],
        "target_price": 0.0, "entry_zone": {"low": 0.0, "high": 0.0},
        "timeframe_days": 0, "supporting_data": "", "error": reason,
    }
    bear_base = {
        "symbol": symbol, "counter_thesis": "", "risks": [],
        "downside_target": 0.0, "stop_recommended": 0.0,
        "invalidation_signal": "", "confidence": 0.0, "error": reason,
    }
    decision_base = {
        "decision": "NO_TRADE", "symbol": symbol, "confidence": 0.0,
        "position_size_pct": 0.0, "entry_price": 0.0, "stop_loss": 0.0,
        "target_price": 0.0, "risk_reward_ratio": 0.0,
        "reasoning": reason, "thesis_summary": f"NO_TRADE — {reason}",
    }
    return {"bull": base, "bear": bear_base, "decision": decision_base, "combined": True}
