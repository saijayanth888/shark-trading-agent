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
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "PERPLEXITY_API_KEY environment variable is not set. "
            "Obtain a key from https://www.perplexity.ai/ and export it "
            "before running the agent."
        )

    tickers_str = ", ".join(tickers)
    user_prompt = (
        f"For each of these tickers: {tickers_str}, provide: "
        "(1) latest news headlines with sentiment (positive/negative/neutral), "
        "(2) key catalysts in next 5 days, "
        "(3) risk factors, "
        "(4) overall sentiment score from -1.0 to +1.0. "
        "Return as JSON where each key is the ticker symbol and the value is "
        'an object with keys "sentiment_score" (number), "headlines" (array of '
        'strings), "catalysts" (array of strings), "risks" (array of strings).'
    )

    payload: dict[str, Any] = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
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
            result[ticker_upper] = {
                "sentiment_score": float(entry.get("sentiment_score", 0.0)),
                "headlines": list(entry.get("headlines", [])),
                "catalysts": list(entry.get("catalysts", [])),
                "risks": list(entry.get("risks", [])),
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
                "risks": [],
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
        logger.error(
            "Failed to parse Perplexity response as JSON: %s\nRaw content: %.500s",
            exc,
            text,
        )
        return {}
