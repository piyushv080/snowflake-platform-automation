"""Snowflake Cortex adapter for LLMProvider, using AI_COMPLETE.

Why AI_COMPLETE (not SNOWFLAKE.CORTEX.COMPLETE)? The SNOWFLAKE.CORTEX.* names
are deprecated in favor of the top-level AI_* functions.

Structured output: we pass the JSON schema via `response_format`, verified in
Stage 4 to work with bind parameters. Cortex then constrains generation to
the schema. We still re-validate with Pydantic afterwards.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from snowflake.snowpark import Session

from core.exceptions import LLMProviderError
from llm.base import LLMProvider, LLMResponse

_AI_COMPLETE_SQL = """
SELECT AI_COMPLETE(
    model             => ?,
    prompt            => ?,
    model_parameters  => PARSE_JSON(?),
    response_format   => PARSE_JSON(?),
    show_details      => TRUE
) AS RESPONSE
"""


class SnowflakeCortexProvider(LLMProvider):
    name = "snowflake_cortex"

    def __init__(self, session: Session, model: str, fallback_model: Optional[str] = None) -> None:
        self.session = session
        self.model = model
        self.fallback_model = fallback_model

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> LLMResponse:
        # The single-string form of AI_COMPLETE has no separate system role, so the
        # system instructions are placed first, clearly separated from the data.
        prompt = f"{system_prompt}\n\n=== INPUT ===\n{user_prompt}"
        model_parameters = json.dumps({"temperature": temperature, "max_tokens": max_tokens})
        response_format = json.dumps({"type": "json", "schema": json_schema})

        errors = []
        for model in [m for m in (self.model, self.fallback_model) if m]:
            try:
                rows = self.session.sql(
                    _AI_COMPLETE_SQL, params=[model, prompt, model_parameters, response_format]
                ).collect()
                return self._to_response(rows[0]["RESPONSE"], model)
            except Exception as error:  # noqa: BLE001 - surfaced below with context
                errors.append(f"{model}: {error}")
        raise LLMProviderError("AI_COMPLETE failed for all models -> " + " | ".join(errors))

    @staticmethod
    def _to_response(raw: Any, model: str) -> LLMResponse:
        """Unwrap the show_details envelope: structured_output[0].raw_message."""
        details = json.loads(raw) if isinstance(raw, str) else raw
        parsed = None
        text = raw if isinstance(raw, str) else json.dumps(raw)
        try:
            message = details["structured_output"][0]["raw_message"]
            if isinstance(message, dict):
                parsed = message
                text = json.dumps(message)
            else:
                text = str(message)
        except (KeyError, IndexError, TypeError):
            pass  # leave text as the raw envelope; the parser will report the problem
        return LLMResponse(
            text=text,
            model=details.get("model", model) if isinstance(details, dict) else model,
            parsed=parsed,
            usage=details.get("usage", {}) if isinstance(details, dict) else {},
        )
