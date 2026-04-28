"""
shark/data/perplexity.py
------------------------
Fetches market intelligence for a list of tickers using the Perplexity
Sonar-Pro API.  The API is called via plain ``requests`` — no Perplexity
SDK required.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar-pro"
_SYSTEM_PROMPT = (
    "You are a financial research assistant. "
    "Provide factual, cited analysis only."
)
_MAX_RETRIES = 3
_BACKOFF_SECONDS = 2
_MAX_TICKERS_PER_BATCH = 6


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_market_intel(tickers: list[str]) -> dict[str, Any]:
    """Fetch market intelligence for a list of stock tickers.

    Calls the Perplexity Sonar-Pro API and asks for:
      1. Latest news headlines with sentiment.
      2. Key catalysts in the next 5 days.
      3. Risk factors.
      4. An overall sentiment score from -1.0 to +1.0.

    Parameters
    ----------
    tickers:
        A non-empty list of uppercase ticker symbols, e.g. ``["NVDA", "AAPL"]``.

    Returns
    -------
    dict
        Mapping of ticker → intelligence dict.  Each value contains:
        ``sentiment_score`` (float), ``headlines`` (list[str]),
        ``catalysts`` (list[str]), ``risks`` (list[str]),
        ``raw_response`` (str).  On parse failure the dict will also
        contain an ``error`` key and ``sentiment_score`` will be 0.0.

    Raises
    ------
    EnvironmentError
        If the ``PERPLEXITY_API_KEY`` environment variable is not set.
    requests.HTTPError
        If all retry attempts are exhausted with a non-2xx status.
    """
    # Batch large watchlists to avoid truncated JSON responses
    if len(tickers) > _MAX_TICKERS_PER_BATCH:
        result: dict[str, Any] = {}
        for i in range(0, len(tickers), _MAX_TICKERS_PER_BATCH):
            batch = tickers[i : i + _MAX_TICKERS_PER_BATCH]
            logger.info("Perplexity batch %d-%d of %d", i + 1, i + len(batch), len(tickers))
            batch_result = _fetch_batch(batch)
            result.update(batch_result)
        return result
    return _fetch_batch(tickers)


def _fetch_batch(tickers: list[str]) -> dict[str, Any]:
    """Fetch intel for a single batch of tickers (max ~6)."""
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "PERPLEXITY_API_KEY environment variable is not set. "
            "Obtain a key from https://www.perplexity.ai/ and export it "
            "before running the agent."
        )

    tickers_str = ", ".join(tickers)
    user_prompt = (
        f"For each of these stock tickers: {tickers_str}. For each ticker provide: "
        "(1) 2-3 specific news headlines with positive/negative/neutral sentiment — cite the actual source and date. "
        "(2) The SPECIFIC catalyst driving price action TODAY — name the exact event, announcement, product launch, or data point. "
        "Write 'no specific catalyst — general momentum only' if there is nothing concrete. "
        "(3) Whether this catalyst is ALREADY PRICED IN to the current price (yes/no — consider if stock already moved >3% on this news). "
        "(4) Risk factors and specific signals that would INVALIDATE a bullish thesis. "
        "(5) Days until next earnings report: 0=today, 1=tomorrow, 2-7=this week, null if more than 7 days away or unknown. "
        "(6) Analyst consensus: buy, hold, or sell. "
        "(7) Overall sentiment score from -1.0 (very bearish) to +1.0 (very bullish). "
        "Return ONLY valid JSON — no markdown, no explanation — where each key is the uppercase ticker symbol "
        "and the value has these exact keys: "
        '"sentiment_score" (number -1 to 1), '
        '"headlines" (array of strings), '
        '"catalysts" (array of strings — specific events only, not vague), '
        '"catalyst_specific" (boolean: true only if there is a concrete datable news event today), '
        '"catalyst_priced_in" (boolean: true if stock already moved significantly on this news), '
        '"risks" (array of strings), '
        '"invalidation_signals" (array of strings — what would break the bullish thesis), '
        '"earnings_within_days" (integer 0-7 or null if beyond 7 days), '
        '"analyst_rating" (string: "buy", "hold", or "sell").'
    )

    payload: dict[str, Any] = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 4096,
        "return_citations": True,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    raw_content: str = ""

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = requests.post(
                _API_URL,
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
            break  # success — exit retry loop
        except requests.HTTPError as exc:
            logger.warning(
                "Perplexity API HTTP error on attempt %d/%d: %s",
                attempt,
                _MAX_RETRIES,
                exc,
            )
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(_BACKOFF_SECONDS)
        except (requests.ConnectionError, requests.Timeout) as exc:
            logger.warning(
                "Perplexity API connection error on attempt %d/%d: %s",
                attempt,
                _MAX_RETRIES,
                exc,
            )
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(_BACKOFF_SECONDS)

    # ------------------------------------------------------------------
    # Parse the model's response as JSON.
    # The model sometimes wraps JSON in a markdown code block — strip it.
    # ------------------------------------------------------------------
    parsed: dict[str, Any] = _extract_json(raw_content)

    result: dict[str, Any] = {}
    for ticker in tickers:
        ticker_upper = ticker.upper()
        if ticker_upper in parsed:
            entry = parsed[ticker_upper]
            raw_days = entry.get("earnings_within_days")
            earnings_days: int | None = (
                int(raw_days) if raw_days is not None and str(raw_days).isdigit() else None
            )
            result[ticker_upper] = {
                "sentiment_score": float(entry.get("sentiment_score") or 0.0),
                "headlines": list(entry.get("headlines") or []),
                "catalysts": list(entry.get("catalysts") or []),
                "catalyst_specific": bool(entry.get("catalyst_specific") or False),
                "catalyst_priced_in": bool(entry.get("catalyst_priced_in") or False),
                "risks": list(entry.get("risks") or []),
                "invalidation_signals": list(entry.get("invalidation_signals") or []),
                "earnings_within_days": earnings_days,
                "analyst_rating": str(entry.get("analyst_rating") or "hold").lower(),
                "raw_response": raw_content,
            }
        else:
            logger.warning(
                "Ticker %s not found in Perplexity response; using defaults.",
                ticker_upper,
            )
            result[ticker_upper] = {
                "sentiment_score": 0.0,
                "headlines": [],
                "catalysts": [],
                "catalyst_specific": False,
                "catalyst_priced_in": False,
                "risks": [],
                "invalidation_signals": [],
                "earnings_within_days": None,
                "analyst_rating": "hold",
                "raw_response": raw_content,
                "error": f"Ticker {ticker_upper} missing from API response",
            }

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any]:
    """Attempt to extract and parse a JSON object from *text*.

    Handles the common case where the model wraps the JSON in a markdown
    fenced code block (```json ... ```).
    """
    # Strip markdown fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Drop first line (```json or ```) and last line (```)
        inner_lines = lines[1:] if lines[-1].strip() == "```" else lines[1:]
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        cleaned = "\n".join(inner_lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Full JSON parse failed (%s) — attempting truncated JSON salvage",
            exc,
        )
        # Try to salvage truncated JSON by finding the last complete ticker block
        salvaged = _salvage_truncated_json(cleaned)
        if salvaged:
            logger.info("Salvaged %d tickers from truncated response", len(salvaged))
            return salvaged
        logger.error(
            "JSON salvage also failed. Raw content: %.500s", text,
        )
        return {}


def _salvage_truncated_json(text: str) -> dict[str, Any] | None:
    """Try to recover partial data from truncated JSON.

    Strategy: find the last complete '},' or '}' before the truncation
    and close the outer object.
    """
    # Find last complete ticker block ending with }
    last_close = text.rfind("}")
    if last_close <= 0:
        return None

    # Try progressively shorter substrings
    for end_pos in range(last_close, max(last_close - 500, 0), -1):
        candidate = text[:end_pos + 1]
        # Ensure we have an outer closing brace
        if not candidate.rstrip().endswith("}"):
            candidate = candidate.rstrip().rstrip(",") + "}"
        try:
            result = json.loads(candidate)
            if isinstance(result, dict) and result:
                return result
        except json.JSONDecodeError:
            continue
    return None
