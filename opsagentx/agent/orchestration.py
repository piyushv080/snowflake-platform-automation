"""Agent orchestration layer.

`AgentRunner` is the interface: "given an incident, let an agent investigate
with its tools and return its final answer plus a trace of what it did".

* CortexAgentRunner (here)  - Snowflake-managed Cortex Agent, called through
  SNOWFLAKE.CORTEX.DATA_AGENT_RUN in SQL.
* LangGraphAgentRunner (future, local) - a LangGraph graph whose tool nodes
  call agent/tools.py DiagnosticTools and whose LLM node calls OllamaProvider.

Why DATA_AGENT_RUN instead of the REST API?
  Snowflake recommends the streaming REST API for most integrations. We use the
  SQL wrapper because (1) it reuses the already-authenticated Snowpark session,
  so no token handling is needed in app code, (2) it works the same from a
  sandbox, a stored procedure, and both Streamlit runtimes, and (3) in this
  sandbox outbound HTTPS from Python fails TLS verification through a proxy.
  The trade-off: no streaming, so the UI shows a spinner until the run ends.

The agent's answer is NOT trusted: the caller validates it with Pydantic and
re-runs the policy engine, exactly as for the direct LLM path.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent.tools import TOOL_NAMES
from core.exceptions import LLMProviderError

if TYPE_CHECKING:
    from snowflake.snowpark import Session


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class AgentRun:
    final_text: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    runbook_ids: list[str] = field(default_factory=list)
    unexpected_tools: list[str] = field(default_factory=list)   # should always be empty
    usage: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tool_calls]


class AgentRunner(ABC):
    name = "abstract"

    @abstractmethod
    def run(self, incident_id: str) -> AgentRun: ...


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_agent_response(response: dict[str, Any]) -> AgentRun:
    """Extract final answer, tool trace, and retrieved runbook ids from an agent response."""
    if response.get("status") not in (None, "completed"):
        raise LLMProviderError(f"agent run ended with status {response.get('status')!r}")

    tool_calls: list[ToolCall] = []
    runbook_ids: list[str] = []
    texts: list[str] = []
    for item in response.get("content", []):
        kind = item.get("type")
        if kind == "tool_use":
            tool = _maybe_json(item["tool_use"])
            tool_calls.append(ToolCall(name=tool.get("name", "?"), arguments=tool.get("input", {})))
        elif kind == "tool_result":
            result = _maybe_json(item["tool_result"])
            for part in result.get("content", []) if isinstance(result, dict) else []:
                for hit in (part.get("json") or {}).get("search_results", []):
                    if hit.get("doc_id") and hit["doc_id"] not in runbook_ids:
                        runbook_ids.append(hit["doc_id"])
        elif kind == "text":
            texts.append(item.get("text", ""))

    if not texts:
        raise LLMProviderError("agent returned no final text")
    metadata = response.get("metadata") or {}
    return AgentRun(
        final_text=texts[-1],
        model=metadata.get("orchestration_model", "cortex-agent"),
        tool_calls=tool_calls,
        runbook_ids=runbook_ids,
        unexpected_tools=[c.name for c in tool_calls if c.name not in TOOL_NAMES],
        usage=metadata.get("usage", {}),
    )


class CortexAgentRunner(AgentRunner):
    name = "cortex_agent"

    def __init__(self, session: "Session", agent_fqn: str) -> None:
        self.session = session
        self.agent_fqn = agent_fqn

    def run(self, incident_id: str) -> AgentRun:
        body = {
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": f"Diagnose incident {incident_id}."}],
            }],
            "stream": False,
        }
        try:
            rows = self.session.sql(
                "SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(?, ?) AS R",
                params=[self.agent_fqn, json.dumps(body)],
            ).collect()
        except Exception as error:  # noqa: BLE001
            raise LLMProviderError(f"Cortex Agent call failed: {error}") from error
        raw = rows[0]["R"]
        run = parse_agent_response(json.loads(raw) if isinstance(raw, str) else raw)
        run.raw = raw if isinstance(raw, str) else json.dumps(raw)
        return run
