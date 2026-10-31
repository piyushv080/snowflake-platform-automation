"""Central configuration.

Values come from environment variables when present, otherwise defaults.
Nothing secret lives here: inside Snowflake the session is already
authenticated, and the future local version will read secrets from the
environment (never from source code).
"""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # --- Snowflake object locations -----------------------------------
    database: str = _env("OPSAGENTX_DATABASE", "OPSAGENTX_DB")
    schema: str = _env("OPSAGENTX_SCHEMA", "OPSAGENTX_SCHEMA")

    # --- LLM ----------------------------------------------------------
    # claude-sonnet-4-5 and llama3.3-70b were verified to respond in this
    # account during Stage 1. mistral-large2 is legacy and rejected.
    llm_model: str = _env("OPSAGENTX_LLM_MODEL", "claude-sonnet-4-5")
    llm_fallback_model: str = _env("OPSAGENTX_LLM_FALLBACK_MODEL", "llama3.3-70b")

    # --- Agent / search object names ------------------------------------
    agent_name: str = _env("OPSAGENTX_AGENT", "OPSAGENTX_DIAGNOSTIC_AGENT")
    search_service: str = _env("OPSAGENTX_SEARCH_SERVICE", "RUNBOOK_SEARCH")

    # --- Diagnosis rules ------------------------------------------------
    # Below this confidence we still store the diagnosis but flag it for review.
    min_confidence: float = float(_env("OPSAGENTX_MIN_CONFIDENCE", "0.5"))
    log_lookback_limit: int = int(_env("OPSAGENTX_LOG_LIMIT", "50"))

    # Environments where ANY remediation needs a human approval.
    # We require approval everywhere for this demo; production can never be relaxed.
    approval_required_environments: tuple = field(
        default_factory=lambda: ("development", "test", "production")
    )

    def fq(self, object_name: str) -> str:
        """Fully qualify a table/object name, e.g. fq('INCIDENTS')."""
        return f"{self.database}.{self.schema}.{object_name}"


settings = Settings()
