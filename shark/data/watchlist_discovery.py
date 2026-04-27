"""
shark/data/watchlist_discovery.py
----------------------------------
LLM-powered watchlist expansion engine.

Runs weekly (during weekly-review) to discover high-momentum stocks outside
the core watchlist.  Uses Perplexity Sonar-Pro for market scanning and applies
strict guardrails before adding any ticker to the dynamic watchlist.

Guardrails:
  - Market cap ≥ $10 B
  - Average daily volume ≥ 1 M shares
  - Must map to a tracked sector (see SECTOR_ETFS)
  - Max 10 dynamic tickers at any time
  - Entries expire after 14 days
  - Never duplicates a core watchlist ticker
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar-pro"
_MAX_RETRIES = 3
_BACKOFF_SECONDS = 2

# Guardrail thresholds
MIN_MARKET_CAP_B = 10        # $10 billion
MIN_AVG_VOLUME_M = 1         # 1 million shares/day
MAX_SUGGESTIONS = 10          # Max tickers per discovery run


# ---------------------------------------------------------------------------
# Discovery prompt
# ---------------------------------------------------------------------------

_DISCOVERY_SYSTEM = (
    "You are a quantitative equity screener. "
    "Return ONLY valid JSON arrays. No markdown, no explanation."
)

_DISCOVERY_PROMPT = """\
Scan the US stock market for swing trading opportunities. I already track these \
tickers: {existing_tickers}. DO NOT include any of them.

Find {count} stocks that meet ALL of these criteria:
1. US-listed common stock (no ETFs, ADRs, SPACs, or penny stocks)
2. Market cap above $10 billion
3. Average daily volume above 1 million shares
4. Currently showing strong momentum: 52-week high, breakout, or sector rotation
5. Has a clear near-term catalyst (earnings beat, product launch, sector tailwind, analyst upgrade)
6. Belongs to one of these sectors: {sectors}

Current market context: {market_context}

Return a JSON array where each element has these exact keys:
- "symbol": uppercase ticker (string)
- "sector": one of the allowed sectors listed above (string)
- "market_cap_b": approximate market cap in billions (number)
- "avg_volume_m": approximate average daily volume in millions (number)
- "catalyst": one-sentence description of the current catalyst (string)
- "momentum_signal": what's driving the momentum (string)
- "reason": why this stock is a good swing trade candidate right now (string)

Return ONLY the JSON array. No markdown fencing, no explanation.
"""


# ---------------------------------------------------------------------------
# Discovery engine
# ---------------------------------------------------------------------------

def discover_tickers(
    existing_tickers: list[str],
    market_context: str = "",
    count: int = 8,
) -> list[dict[str, Any]]:
    """Call Perplexity to discover new swing trade candidates.

    Parameters
    ----------
    existing_tickers:
        Tickers already in the watchlist (core + dynamic). These will be
        excluded from suggestions.
    market_context:
        Brief string describing current regime, recent performance, etc.
        Injected into the prompt for better results.
    count:
        Number of suggestions to request (max 10).

    Returns
    -------
    list[dict]
        Validated suggestions that passed all guardrails.
        Empty list if discovery fails or no valid candidates found.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        logger.warning("PERPLEXITY_API_KEY not set — skipping watchlist discovery")
        return []

    count = min(count, MAX_SUGGESTIONS)
    from shark.data.watchlist import ALLOWED_SECTORS
    sectors_str = ", ".join(sorted(ALLOWED_SECTORS))

    prompt = _DISCOVERY_PROMPT.format(
        existing_tickers=", ".join(existing_tickers),
        count=count,
        sectors=sectors_str,
        market_context=market_context or "No specific context provided.",
    )

    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _DISCOVERY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "return_citations": True,
    }

    # Call Perplexity with retry
    raw_suggestions = _call_perplexity(api_key, payload)
    if not raw_suggestions:
        return []

    # Apply guardrails
    validated = _apply_guardrails(raw_suggestions, existing_tickers)
    logger.info(
        "Discovery: %d raw suggestions → %d passed guardrails",
        len(raw_suggestions), len(validated),
    )
    return validated


def _call_perplexity(api_key: str, payload: dict) -> list[dict[str, Any]]:
    """Make the Perplexity API call with retry and parse the JSON response."""
    import requests

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(
                _API_URL, json=payload, headers=headers, timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            return _parse_json_response(content)

        except requests.HTTPError as exc:
            logger.warning(
                "Discovery API attempt %d/%d failed (HTTP %s): %s",
                attempt, _MAX_RETRIES, getattr(exc.response, 'status_code', '?'), exc,
            )
        except Exception as exc:
            logger.warning(
                "Discovery API attempt %d/%d failed: %s",
                attempt, _MAX_RETRIES, exc,
            )

        if attempt < _MAX_RETRIES:
            time.sleep(_BACKOFF_SECONDS * attempt)

    logger.error("Discovery API failed after %d attempts", _MAX_RETRIES)
    return []


def _parse_json_response(content: str) -> list[dict[str, Any]]:
    """Extract a JSON array from the API response text.

    Handles markdown-fenced code blocks and raw JSON.
    """
    import re

    # Try extracting from ```json ... ``` block
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", content, re.DOTALL)
    text_to_parse = json_match.group(1).strip() if json_match else content.strip()

    # Try direct parse
    try:
        result = json.loads(text_to_parse)
        if isinstance(result, list):
            return result
        logger.warning("Discovery response is not a JSON array")
        return []
    except json.JSONDecodeError:
        pass

    # Try finding array within text
    arr_match = re.search(r"\[.*\]", text_to_parse, re.DOTALL)
    if arr_match:
        try:
            result = json.loads(arr_match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse discovery response as JSON array")
    return []


def _apply_guardrails(
    suggestions: list[dict[str, Any]],
    existing_tickers: list[str],
) -> list[dict[str, Any]]:
    """Filter suggestions through all guardrails.

    Returns only tickers that pass every check.
    """
    from shark.data.watchlist import ALLOWED_SECTORS

    existing_set = set(existing_tickers)
    validated: list[dict[str, Any]] = []

    for item in suggestions:
        symbol = (item.get("symbol") or "").upper().strip()
        sector = (item.get("sector") or "").strip()
        market_cap = float(item.get("market_cap_b", 0) or 0)
        avg_volume = float(item.get("avg_volume_m", 0) or 0)
        catalyst = item.get("catalyst", "")
        reason = item.get("reason", "")

        # Skip if missing required fields
        if not symbol or not sector:
            logger.debug("Skipping suggestion — missing symbol or sector: %s", item)
            continue

        # Symbol validation
        if not symbol.isalpha() or len(symbol) > 5:
            logger.debug("Skipping %s — invalid symbol format", symbol)
            continue

        # Duplicate check
        if symbol in existing_set:
            logger.debug("Skipping %s — already in watchlist", symbol)
            continue

        # Market cap guardrail
        if market_cap < MIN_MARKET_CAP_B:
            logger.info(
                "Skipping %s — market cap $%.1fB < $%dB minimum",
                symbol, market_cap, MIN_MARKET_CAP_B,
            )
            continue

        # Volume guardrail
        if avg_volume < MIN_AVG_VOLUME_M:
            logger.info(
                "Skipping %s — avg volume %.1fM < %dM minimum",
                symbol, avg_volume, MIN_AVG_VOLUME_M,
            )
            continue

        # Sector guardrail
        if sector not in ALLOWED_SECTORS:
            logger.info("Skipping %s — sector '%s' not in allowed list", symbol, sector)
            continue

        # Catalyst required
        if not catalyst and not reason:
            logger.info("Skipping %s — no catalyst or reason provided", symbol)
            continue

        validated.append({
            "symbol": symbol,
            "sector": sector,
            "market_cap_b": market_cap,
            "avg_volume_m": avg_volume,
            "catalyst": catalyst,
            "momentum_signal": item.get("momentum_signal", ""),
            "reason": reason,
        })

        existing_set.add(symbol)  # prevent duplicates within batch

    return validated


# ---------------------------------------------------------------------------
# Integration: run full discovery cycle and persist results
# ---------------------------------------------------------------------------

def run_discovery_cycle(
    market_context: str = "",
    count: int = 8,
) -> list[dict[str, Any]]:
    """Full discovery cycle: discover → merge with existing → persist.

    Call this from the weekly-review phase.

    Parameters
    ----------
    market_context:
        Brief description of current market conditions for the LLM.
    count:
        Number of new tickers to request.

    Returns
    -------
    list[dict]
        The newly added entries (after guardrails + deduplication).
    """
    from shark.data.watchlist import (
        get_core_watchlist,
        get_dynamic_entries,
        save_dynamic_watchlist,
        DYNAMIC_EXPIRY_DAYS,
    )

    core = get_core_watchlist()
    existing_dynamic = get_dynamic_entries()
    existing_dynamic_symbols = [e.get("symbol", "") for e in existing_dynamic]
    all_existing = core + existing_dynamic_symbols

    # Run LLM discovery
    new_suggestions = discover_tickers(
        existing_tickers=all_existing,
        market_context=market_context,
        count=count,
    )

    if not new_suggestions:
        logger.info("Discovery cycle: no new tickers found")
        # Still prune expired entries
        _prune_and_save(existing_dynamic)
        return []

    # Build new entries with metadata
    today = date.today()
    expires = today + timedelta(days=DYNAMIC_EXPIRY_DAYS)

    new_entries = []
    for suggestion in new_suggestions:
        entry = {
            "symbol": suggestion["symbol"],
            "sector": suggestion["sector"],
            "source": "perplexity_discovery",
            "added_date": today.isoformat(),
            "expires_date": expires.isoformat(),
            "market_cap_b": suggestion.get("market_cap_b", 0),
            "avg_volume_m": suggestion.get("avg_volume_m", 0),
            "catalyst": suggestion.get("catalyst", ""),
            "momentum_signal": suggestion.get("momentum_signal", ""),
            "reason": suggestion.get("reason", ""),
        }
        new_entries.append(entry)

    # Merge: keep active existing + add new (save_dynamic_watchlist enforces limits)
    merged = existing_dynamic + new_entries
    save_dynamic_watchlist(merged)

    logger.info(
        "Discovery cycle complete: %d new tickers added — %s",
        len(new_entries),
        [e["symbol"] for e in new_entries],
    )
    return new_entries


def _prune_and_save(entries: list[dict[str, Any]]) -> None:
    """Remove expired entries and re-save."""
    from shark.data.watchlist import save_dynamic_watchlist

    today = date.today().isoformat()
    active = [e for e in entries if e.get("expires_date", "2000-01-01") >= today]

    if len(active) < len(entries):
        logger.info(
            "Pruned %d expired dynamic entries", len(entries) - len(active),
        )
        save_dynamic_watchlist(active)
