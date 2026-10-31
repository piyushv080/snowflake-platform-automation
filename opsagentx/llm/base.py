"""LLM provider interface.

Business logic talks to `LLMProvider`, never to Snowflake Cortex directly.

Implementations:
* SnowflakeCortexProvider (llm/snowflake_cortex.py) - AI_COMPLETE in SQL
* OllamaProvider           (future, local)           - POST /api/chat on Ollama

Switching providers is NOT automatic: each adapter handles its own
authentication, model names, and structured-output mechanism. Ollama, for
example, takes a JSON schema in its `format` field and smaller local models
follow schemas less reliably - which is exactly why the caller always
re-validates the result with Pydantic (llm/schemas.py) regardless of provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class LLMResponse:
    """What every provider returns."""

    text: str                                 # raw text (or JSON text) from the model
    model: str                                # the model that actually answered
    parsed: Optional[dict[str, Any]] = None   # provider-parsed JSON, if it did any
    usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Minimal contract an LLM backend must implement."""

    name: str = "abstract"

    @abstractmethod
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> LLMResponse:
        """Ask the model for output that should follow `json_schema`.

        "Should" matters: callers must still validate the output.
        Raises core.exceptions.LLMProviderError if the call itself fails.
        """
