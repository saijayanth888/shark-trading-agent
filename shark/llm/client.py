"""
Multi-provider LLM client abstraction.

Inspired by TradingAgents' provider-agnostic client architecture.
Allows Shark to use different LLM providers for different agent roles:
  - Decision arbiter → Claude (highest quality)
  - Debate rounds → GPT-4o-mini or Gemini Flash (cheaper, faster)
  - Risk review → configurable per use case

Providers:
  - anthropic (default): Claude models via Anthropic API
  - openai: GPT models via OpenAI API
  - google: Gemini models via Google AI API

Usage:
    from shark.llm.client import get_llm_client
    client = get_llm_client("anthropic", model="claude-sonnet-4-6")
    response = client.chat("You are an analyst.", "Analyze AAPL")
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LLMResponse:
    """Unified response object across providers."""

    def __init__(self, content: str, model: str, usage: dict[str, int] | None = None):
        self.content = content
        self.model = model
        self.usage = usage or {}

    def __str__(self) -> str:
        return self.content

    def to_json(self) -> dict | None:
        """Try to parse content as JSON. Returns None on failure."""
        raw = self.content.strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


class LLMClient(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, model: str, **kwargs):
        self.model = model
        self.kwargs = kwargs

    @abstractmethod
    def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat message and get a response."""
        ...

    @abstractmethod
    def chat_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        max_tokens: int = 1000,
        temperature: float = 0.3,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat message with tool-use schemas for structured output."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        ...


# ---------------------------------------------------------------------------
# Anthropic (Claude)
# ---------------------------------------------------------------------------

class AnthropicClient(LLMClient):
    """Claude via the Anthropic API."""

    def __init__(self, model: str = "claude-sonnet-4-6", **kwargs):
        super().__init__(model, **kwargs)
        try:
            import anthropic
            self._lib = anthropic
            self._client = anthropic.Anthropic(
                api_key=kwargs.get("api_key", os.environ.get("ANTHROPIC_API_KEY")),
            )
        except ImportError:
            raise ImportError("anthropic package required: pip install anthropic")

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def chat(self, system_prompt, user_message, max_tokens=1000, temperature=0.3, **kwargs):
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=[{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return LLMResponse(response.content[0].text, self.model, usage)

    def chat_with_tools(self, system_prompt, user_message, tools, max_tokens=1000,
                        temperature=0.3, **kwargs):
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=[{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
            tools=tools,
            tool_choice={"type": "any"},
        )
        # Extract tool-use result as JSON string
        for block in response.content:
            if block.type == "tool_use":
                content = json.dumps(block.input)
                usage = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
                return LLMResponse(content, self.model, usage)
        # Fallback to text
        return self.chat(system_prompt, user_message, max_tokens, temperature)


# ---------------------------------------------------------------------------
# OpenAI (GPT)
# ---------------------------------------------------------------------------

class OpenAIClient(LLMClient):
    """GPT models via the OpenAI API."""

    def __init__(self, model: str = "gpt-4o-mini", **kwargs):
        super().__init__(model, **kwargs)
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=kwargs.get("api_key", os.environ.get("OPENAI_API_KEY")),
            )
        except ImportError:
            raise ImportError("openai package required: pip install openai")

    @property
    def provider_name(self) -> str:
        return "openai"

    def chat(self, system_prompt, user_message, max_tokens=1000, temperature=0.3, **kwargs):
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        content = response.choices[0].message.content or ""
        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
        }
        return LLMResponse(content, self.model, usage)

    def chat_with_tools(self, system_prompt, user_message, tools, max_tokens=1000,
                        temperature=0.3, **kwargs):
        # Convert Anthropic tool format to OpenAI function format
        functions = []
        for tool in tools:
            functions.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })

        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=functions,
            tool_choice="required",
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            content = msg.tool_calls[0].function.arguments
            usage = {
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            }
            return LLMResponse(content, self.model, usage)
        return self.chat(system_prompt, user_message, max_tokens, temperature)


# ---------------------------------------------------------------------------
# Google (Gemini)
# ---------------------------------------------------------------------------

class GoogleClient(LLMClient):
    """Gemini models via the Google Generative AI API."""

    def __init__(self, model: str = "gemini-2.0-flash", **kwargs):
        super().__init__(model, **kwargs)
        try:
            import google.generativeai as genai
            genai.configure(api_key=kwargs.get("api_key", os.environ.get("GOOGLE_API_KEY")))
            self._genai = genai
        except ImportError:
            raise ImportError("google-generativeai package required: pip install google-generativeai")

    @property
    def provider_name(self) -> str:
        return "google"

    def chat(self, system_prompt, user_message, max_tokens=1000, temperature=0.3, **kwargs):
        model = self._genai.GenerativeModel(
            self.model,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            user_message,
            generation_config=self._genai.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return LLMResponse(response.text, self.model)

    def chat_with_tools(self, system_prompt, user_message, tools, max_tokens=1000,
                        temperature=0.3, **kwargs):
        # Gemini structured output — fall back to plain chat with JSON instruction
        enhanced_prompt = (
            f"{user_message}\n\nRespond with valid JSON matching the required schema."
        )
        return self.chat(system_prompt, enhanced_prompt, max_tokens, temperature)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "google": GoogleClient,
}


def get_llm_client(
    provider: str | None = None,
    model: str | None = None,
    **kwargs,
) -> LLMClient:
    """
    Create an LLM client for the specified provider.

    Args:
        provider: One of "anthropic", "openai", "google". Defaults to env SHARK_LLM_PROVIDER or "anthropic".
        model: Model name. Defaults to provider-specific default.
        **kwargs: Additional kwargs passed to the client (e.g., api_key).

    Returns:
        LLMClient instance.
    """
    provider = provider or os.environ.get("SHARK_LLM_PROVIDER", "anthropic")
    provider = provider.lower()

    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider: {provider}. "
            f"Available: {', '.join(_PROVIDERS.keys())}"
        )

    if model is None:
        model_defaults = {
            "anthropic": os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            "openai": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "google": os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash"),
        }
        model = model_defaults.get(provider, "")

    logger.info("Creating LLM client: provider=%s model=%s", provider, model)
    return cls(model=model, **kwargs)


# Role-based client helpers
def get_debate_client(**kwargs) -> LLMClient:
    """Get the LLM client configured for debate rounds (can be a cheaper model)."""
    provider = os.environ.get("SHARK_DEBATE_LLM_PROVIDER",
                              os.environ.get("SHARK_LLM_PROVIDER", "anthropic"))
    model = os.environ.get("SHARK_DEBATE_LLM_MODEL")
    return get_llm_client(provider=provider, model=model, **kwargs)


def get_arbiter_client(**kwargs) -> LLMClient:
    """Get the LLM client for the decision arbiter (highest quality)."""
    provider = os.environ.get("SHARK_ARBITER_LLM_PROVIDER",
                              os.environ.get("SHARK_LLM_PROVIDER", "anthropic"))
    model = os.environ.get("SHARK_ARBITER_LLM_MODEL")
    return get_llm_client(provider=provider, model=model, **kwargs)


def get_risk_client(**kwargs) -> LLMClient:
    """Get the LLM client for risk review."""
    provider = os.environ.get("SHARK_RISK_LLM_PROVIDER",
                              os.environ.get("SHARK_LLM_PROVIDER", "anthropic"))
    model = os.environ.get("SHARK_RISK_LLM_MODEL")
    return get_llm_client(provider=provider, model=model, **kwargs)
