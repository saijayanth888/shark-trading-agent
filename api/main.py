"""
Shark Trading Agent — FastAPI REST API.

Provides health checks, portfolio inspection, signal history, and a webhook
stub for future integrations. Enables CORS for all origins so a dashboard
can connect from any host.
"""

import os
import re
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Shark Trading Agent API",
    version="0.1.0",
    description=(
        "REST interface for the Shark Trading Agent — inspect portfolio, "
        "retrieve signals, and trigger webhooks."
    ),
)

# Allow all origins for future dashboard integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Memory file paths
_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # shark-trading-agent/
_RESEARCH_LOG = _PROJECT_ROOT / "memory" / "RESEARCH-LOG.md"


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class TradeWebhookBody(BaseModel):
    symbol: str
    action: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_research_sections(text: str) -> list[dict[str, Any]]:
    """
    Parse RESEARCH-LOG.md into a list of section dicts.

    Each section starts with ## YYYY-MM-DD — SYMBOL.
    """
    sections: list[dict[str, Any]] = []

    # Split on section headers
    parts = re.split(r"(?=^## \d{4}-\d{2}-\d{2})", text, flags=re.MULTILINE)

    for part in parts:
        part = part.strip()
        if not part.startswith("## "):
            continue

        lines = part.splitlines()
        header = lines[0].replace("## ", "", 1)

        # Parse "YYYY-MM-DD — SYMBOL"
        header_match = re.match(r"(\d{4}-\d{2}-\d{2})\s*[—\-]+\s*(.+)", header)
        date_str = header_match.group(1) if header_match else ""
        symbol = header_match.group(2).strip() if header_match else header

        body: dict[str, Any] = {
            "date": date_str,
            "symbol": symbol,
            "raw": part,
        }

        # Extract key fields
        for line in lines[1:]:
            m = re.match(r"\*\*Sentiment:\*\*\s*(.+)", line)
            if m:
                body["sentiment"] = m.group(1).strip()

            m = re.match(r"\*\*Thesis:\*\*\s*(.+)", line)
            if m:
                body["thesis"] = m.group(1).strip()

            m = re.match(
                r"\*\*Entry:\*\*\s*([\d.]+)\s*\|.*\*\*Stop:\*\*\s*([\d.]+)"
                r"\s*\|.*\*\*Target:\*\*\s*([\d.]+)",
                line,
            )
            if m:
                body["entry"] = float(m.group(1))
                body["stop"] = float(m.group(2))
                body["target"] = float(m.group(3))

        sections.append(body)

    return sections


def _get_alpaca_data() -> tuple[dict, list]:
    """
    Import and call alpaca_data helpers. Returns (account, positions).
    Raises HTTPException 503 on failure.
    """
    try:
        from shark.data import alpaca_data  # type: ignore
        account = alpaca_data.get_account()
        positions = alpaca_data.get_positions()
        return account, positions
    except ImportError as exc:
        logger.error("alpaca_data module not available: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="alpaca_data module not available.",
        ) from exc
    except Exception as exc:
        logger.error("Error fetching Alpaca data: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Error fetching Alpaca data: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check() -> dict[str, str]:
    """
    Returns agent liveness status and current trading mode.

    Trading mode is read from the TRADING_MODE environment variable.
    Default is "paper".
    """
    return {
        "status": "ok",
        "version": "0.1.0",
        "mode": os.getenv("TRADING_MODE", "paper"),
    }


@app.get("/portfolio")
def get_portfolio() -> dict[str, Any]:
    """
    Returns current Alpaca account info combined with open positions.

    Calls alpaca_data.get_account() and alpaca_data.get_positions().
    """
    account, positions = _get_alpaca_data()
    return {
        "account": account,
        "positions": positions,
        "position_count": len(positions),
    }


@app.get("/signals/latest")
def get_latest_signal() -> dict[str, Any]:
    """
    Returns the most recent research entry from RESEARCH-LOG.md as a dict.

    Raises 404 if the log does not exist or contains no sections.
    """
    if not _RESEARCH_LOG.exists():
        raise HTTPException(
            status_code=404, detail="RESEARCH-LOG.md not found."
        )

    text = _RESEARCH_LOG.read_text(encoding="utf-8")
    sections = _parse_research_sections(text)

    if not sections:
        raise HTTPException(
            status_code=404, detail="No research entries found."
        )

    return sections[-1]


@app.get("/signals/history")
def get_signal_history() -> dict[str, Any]:
    """
    Returns the last 10 research entries from RESEARCH-LOG.md.

    Raises 404 if the log does not exist.
    """
    if not _RESEARCH_LOG.exists():
        raise HTTPException(
            status_code=404, detail="RESEARCH-LOG.md not found."
        )

    text = _RESEARCH_LOG.read_text(encoding="utf-8")
    sections = _parse_research_sections(text)

    last_10 = sections[-10:] if len(sections) > 10 else sections

    return {
        "count": len(last_10),
        "signals": last_10,
    }


@app.post("/webhook/trade")
def trade_webhook(body: TradeWebhookBody) -> dict[str, Any]:
    """
    Stub endpoint that accepts a trade webhook payload.

    Intended for future integration with external signal providers.
    Currently logs the payload and returns an acknowledgement.

    Args (JSON body):
        symbol: Ticker symbol.
        action: Trade action (e.g. "BUY", "SELL").
    """
    logger.info(
        "Webhook received — symbol=%s action=%s", body.symbol, body.action
    )
    return {
        "received": True,
        "status": "stub",
        "symbol": body.symbol,
        "action": body.action,
    }
