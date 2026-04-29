"""
Pydantic schemas for structured LLM output.

Inspired by TradingAgents' schema design — every decision-making agent
produces a typed Pydantic model so that:
  - Outputs follow consistent structure across runs
  - Claude's tool-use mode enforces schema compliance (no JSON parse errors)
  - Render helpers convert back to the dict shape the rest of the system consumes

Usage:
    from shark.agents.schemas import BullThesis, BearThesis, TradeDecision
    from shark.agents.schemas import RiskPerspective, PortfolioRating
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

class PortfolioRating(str, Enum):
    """5-tier rating used by the decision arbiter and risk reviewer."""
    BUY = "BUY"
    OVERWEIGHT = "OVERWEIGHT"
    HOLD = "HOLD"
    UNDERWEIGHT = "UNDERWEIGHT"
    SELL = "SELL"


class TradeAction(str, Enum):
    """3-tier action for final trade decision."""
    BUY = "BUY"
    NO_TRADE = "NO_TRADE"
    WAIT = "WAIT"


class RiskStance(str, Enum):
    """Risk perspective stance."""
    AGGRESSIVE = "AGGRESSIVE"
    CONSERVATIVE = "CONSERVATIVE"
    NEUTRAL = "NEUTRAL"


# ---------------------------------------------------------------------------
# Bull Thesis
# ---------------------------------------------------------------------------

class BullThesis(BaseModel):
    """Structured bullish thesis produced by the bull analyst."""
    symbol: str = Field(description="Ticker symbol being analyzed")
    thesis: str = Field(
        description="2-3 sentence bull case citing specific data points and catalysts"
    )
    catalysts: list[str] = Field(
        default_factory=list,
        description="List of specific catalysts supporting the bull case"
    )
    target_price: float = Field(
        description="Price target based on technical/fundamental analysis"
    )
    entry_zone: dict = Field(
        default_factory=lambda: {"low": 0.0, "high": 0.0},
        description="Recommended entry price range with 'low' and 'high' keys"
    )
    timeframe_days: int = Field(
        default=5,
        description="Expected holding period in trading days"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the bull thesis (0.0-1.0)"
    )
    supporting_data: str = Field(
        default="",
        description="Key data points supporting the thesis"
    )


def render_bull_thesis(thesis: BullThesis) -> dict:
    """Convert BullThesis to the dict format the rest of the system expects."""
    return thesis.model_dump()


# ---------------------------------------------------------------------------
# Bear Thesis
# ---------------------------------------------------------------------------

class BearThesis(BaseModel):
    """Structured bearish counter-thesis produced by the bear analyst."""
    symbol: str = Field(description="Ticker symbol being analyzed")
    counter_thesis: str = Field(
        description="2-3 sentence bear case citing specific risks and data"
    )
    risks: list[str] = Field(
        default_factory=list,
        description="List of specific risks that could cause the trade to fail"
    )
    downside_target: float = Field(
        description="Realistic downside price target"
    )
    stop_recommended: float = Field(
        description="Recommended stop-loss price level"
    )
    invalidation_signal: str = Field(
        default="",
        description="What price action or event would invalidate the bear case"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the bearish view (0.0-1.0)"
    )


def render_bear_thesis(thesis: BearThesis) -> dict:
    """Convert BearThesis to the dict format the rest of the system expects."""
    return thesis.model_dump()


# ---------------------------------------------------------------------------
# Trade Decision
# ---------------------------------------------------------------------------

class TradeDecision(BaseModel):
    """Structured final trade decision from the decision arbiter."""
    decision: TradeAction = Field(
        description="Final action: BUY only if confidence >= 0.70 AND risk_reward >= 2.0"
    )
    symbol: str = Field(description="Ticker symbol")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Decision confidence (0.0-1.0). BUY requires >= 0.70"
    )
    position_size_pct: float = Field(
        ge=0.0, le=20.0,
        description="Recommended portfolio allocation percentage"
    )
    entry_price: float = Field(description="Specific entry price")
    stop_loss: float = Field(description="Specific stop-loss price")
    target_price: float = Field(description="Specific price target")
    risk_reward_ratio: float = Field(
        description="Risk/reward ratio. BUY requires >= 2.0"
    )
    reasoning: str = Field(
        description="2-3 sentence explanation weighing bull vs bear evidence"
    )
    thesis_summary: str = Field(
        description="One-line summary suitable for signal subscribers"
    )


def render_trade_decision(decision: TradeDecision) -> dict:
    """Convert TradeDecision to the dict format the rest of the system expects."""
    d = decision.model_dump()
    d["decision"] = decision.decision.value
    return d


# ---------------------------------------------------------------------------
# Risk Perspective (for LLM risk debate)
# ---------------------------------------------------------------------------

class RiskPerspective(BaseModel):
    """One risk analyst's perspective in the risk debate."""
    stance: RiskStance = Field(
        description="This analyst's risk stance: AGGRESSIVE, CONSERVATIVE, or NEUTRAL"
    )
    assessment: str = Field(
        description="2-4 sentence assessment of the trade from this risk perspective"
    )
    recommended_action: TradeAction = Field(
        description="What this risk analyst recommends"
    )
    position_size_adjustment: float = Field(
        ge=0.0, le=2.0, default=1.0,
        description="Multiplier to apply to position size (0.0 = reject, 1.0 = unchanged, 2.0 = double)"
    )
    key_concern: str = Field(
        default="",
        description="Single most important risk factor from this perspective"
    )
    confidence_adjustment: float = Field(
        ge=-0.3, le=0.3, default=0.0,
        description="Adjustment to confidence score (-0.3 to +0.3)"
    )


class RiskDebateResult(BaseModel):
    """Synthesized result of the 3-way risk debate."""
    final_action: TradeAction = Field(
        description="Consensus action after risk debate"
    )
    final_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Adjusted confidence after risk debate"
    )
    position_size_multiplier: float = Field(
        ge=0.0, le=2.0, default=1.0,
        description="Final position size multiplier"
    )
    summary: str = Field(
        description="2-sentence synthesis of the risk debate outcome"
    )
    vetoed: bool = Field(
        default=False,
        description="True if the risk debate vetoed the trade entirely"
    )


# ---------------------------------------------------------------------------
# Outcome Reflection (for deferred outcome tracking)
# ---------------------------------------------------------------------------

class OutcomeReflection(BaseModel):
    """Structured reflection on a resolved trade outcome."""
    symbol: str = Field(description="Ticker that was traded")
    trade_date: str = Field(description="Date the trade was entered")
    raw_return_pct: float = Field(description="Raw return percentage")
    alpha_vs_spy_pct: float = Field(description="Alpha vs SPY percentage")
    holding_days: int = Field(description="Actual holding period in days")
    directional_correct: bool = Field(
        description="Whether the directional call was correct"
    )
    thesis_assessment: str = Field(
        description="Which part of the investment thesis held or failed"
    )
    lesson: str = Field(
        description="One concrete lesson to apply to the next similar analysis"
    )


def render_outcome_reflection(reflection: OutcomeReflection) -> str:
    """Render reflection to a concise string for LESSONS-LEARNED.md."""
    direction = "correct" if reflection.directional_correct else "wrong"
    return (
        f"{reflection.symbol} ({reflection.trade_date}): "
        f"Direction {direction}, raw {reflection.raw_return_pct:+.1f}%, "
        f"alpha {reflection.alpha_vs_spy_pct:+.1f}%. "
        f"{reflection.thesis_assessment} "
        f"Lesson: {reflection.lesson}"
    )


# ---------------------------------------------------------------------------
# Tool-use schema converter
# ---------------------------------------------------------------------------

def pydantic_to_claude_tool(model_class: type[BaseModel], name: str, description: str) -> dict:
    """Convert a Pydantic model to a Claude tool-use schema.

    Returns the dict suitable for the `tools` parameter of anthropic.messages.create().
    """
    schema = model_class.model_json_schema()
    # Remove $defs and title — Claude doesn't need them
    schema.pop("$defs", None)
    schema.pop("title", None)

    return {
        "name": name,
        "description": description,
        "input_schema": schema,
    }
