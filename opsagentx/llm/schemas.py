"""The structured diagnosis contract and its defensive parser.

The LLM's answer is treated as UNTRUSTED DATA. This module:
1. Extracts JSON even if the model wrapped it in prose or ``` fences.
2. Validates every field with Pydantic (types, ranges, lengths).
3. Raises LLMResponseError with clear reasons when anything is wrong.

Whether the recommended playbook is actually allowed is NOT decided here -
that is the policy engine's job (core/security.py).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.exceptions import LLMResponseError
from core.models import Category, Severity

NO_PLAYBOOK = "none"


class DiagnosisResult(BaseModel):
    """Exactly the JSON shape the model must return."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    incident_id: str
    category: Category
    severity: Severity
    root_cause: str = Field(min_length=5, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: str = Field(min_length=5, max_length=2000)
    recommended_playbook: str = Field(min_length=1, max_length=100)
    requires_human_approval: bool
    evidence: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("recommended_playbook")
    @classmethod
    def _playbook_name_shape(cls, value: str) -> str:
        # A playbook *name*, not a command: e.g. disk_cleanup.yml or "none".
        value = value.strip()
        if value.lower() == NO_PLAYBOOK:
            return NO_PLAYBOOK
        if not re.fullmatch(r"[a-z0-9_\-]+\.ya?ml", value):
            raise ValueError("must be a playbook file name like disk_cleanup.yml, or 'none'")
        return value


def diagnosis_json_schema(allowed_playbooks: list[str]) -> dict[str, Any]:
    """JSON schema sent to the model.

    Constraining recommended_playbook to an enum of allowlisted names is a
    first line of defense. It is not trusted on its own: the policy engine
    re-checks, because a different provider may ignore the enum.
    """
    return {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "category": {"type": "string", "enum": [c.value for c in Category]},
            "severity": {"type": "string", "enum": [s.value for s in Severity]},
            "root_cause": {"type": "string"},
            "confidence": {"type": "number"},
            "recommended_action": {"type": "string"},
            "recommended_playbook": {"type": "string", "enum": sorted(allowed_playbooks) + [NO_PLAYBOOK]},
            "requires_human_approval": {"type": "boolean"},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "incident_id", "category", "severity", "root_cause", "confidence",
            "recommended_action", "recommended_playbook", "requires_human_approval", "evidence",
        ],
    }


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from model text."""
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise LLMResponseError("model response is not a JSON object")


def parse_diagnosis(text: str, parsed: Optional[dict[str, Any]] = None) -> DiagnosisResult:
    """Validate a model response. Prefers provider-parsed JSON when available."""
    data = parsed if isinstance(parsed, dict) else _extract_json_object(text or "")
    try:
        return DiagnosisResult(**data)
    except ValidationError as error:
        reasons = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]
        raise LLMResponseError("invalid diagnosis -> " + "; ".join(reasons)) from error
