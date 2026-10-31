"""Stage 6 tests: agent response parsing and agent/auto diagnosis modes.

Uses a REAL DATA_AGENT_RUN response captured from Snowflake (fixtures/) so the
parser is tested against the actual wire format, not an imagined one.
"""
import json
import os

import pytest

from agent.orchestration import AgentRun, AgentRunner, ToolCall, parse_agent_response
from agent.tools import DiagnosticTools
from core.exceptions import LLMProviderError, LLMResponseError
from core.models import AuditAction, Incident, IncidentStatus
from core.security import PolicyEngine
from services.diagnosis_service import DiagnosisService
from services.knowledge_service import KeywordRunbookRetriever
from tests.test_diagnosis import GOOD, FakeLLM

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "agent_response_inc1003.json")


def load_fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


class FixtureAgent(AgentRunner):
    name = "fixture_agent"

    def __init__(self, run: AgentRun | None = None, error: Exception | None = None):
        self._run, self.error, self.calls = run, error, 0

    def run(self, incident_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self._run


def make_service(repo, audit, incident_service, agent, llm=None):
    policy = PolicyEngine(repo)
    tools = DiagnosticTools(repo, KeywordRunbookRetriever(repo), policy)
    return DiagnosisService(repo, incident_service, tools, policy, llm or FakeLLM(json.dumps(GOOD)),
                            audit, agent_runner=agent)


@pytest.fixture
def inc1003(repo):
    repo.insert_incident(Incident(
        incident_id="INC1003", source="servicenow", server_name="server-03", environment="development",
        severity="critical", category="service_failure",
        description="nginx service stopped unexpectedly on server-03; health check failing.",
    ))
    return "INC1003"


# --- parsing the real agent response -----------------------------------------
def test_real_agent_response_is_parsed():
    run = parse_agent_response(load_fixture())
    assert run.model == "claude-sonnet-4-5"
    assert "get_incident_details" in run.tool_names
    assert "search_runbooks" in run.tool_names
    assert "validate_remediation" in run.tool_names
    assert "analyze_disk_usage" not in run.tool_names       # not a disk incident
    assert run.runbook_ids and run.runbook_ids[0] == "RB-004"
    assert run.unexpected_tools == []
    assert '"incident_id"' in run.final_text


def test_failed_agent_status_raises():
    response = load_fixture()
    response["status"] = "failed"
    with pytest.raises(LLMProviderError):
        parse_agent_response(response)


def test_agent_without_final_text_raises():
    response = load_fixture()
    response["content"] = [c for c in response["content"] if c["type"] != "text"]
    with pytest.raises(LLMProviderError):
        parse_agent_response(response)


# --- agent mode ----------------------------------------------------------------
def test_agent_mode_produces_validated_diagnosis(repo, audit, incident_service, inc1003):
    agent = FixtureAgent(parse_agent_response(load_fixture()))
    diagnosis = make_service(repo, audit, incident_service, agent).diagnose(inc1003, mode="agent")

    assert diagnosis.orchestration_mode == "cortex_agent"
    assert diagnosis.recommended_playbook == "restart_service.yml"
    assert diagnosis.policy_valid                      # re-checked by the Python policy engine
    assert diagnosis.tools_used == agent._run.tool_names
    assert diagnosis.runbooks_used[0] == "RB-004"
    tool_events = [e for e in repo.list_audit_events(inc1003) if e.action == AuditAction.TOOL_CALLED]
    assert len(tool_events) == len(agent._run.tool_calls)
    assert all(e.actor == "opsagentx-agent" for e in tool_events)


def test_agent_answer_still_goes_through_policy(repo, audit, incident_service):
    # Agent claims a valid playbook for prod-db-01; the Python policy engine must still block it.
    repo.insert_incident(Incident(
        incident_id="INC1004", source="monitoring", server_name="prod-db-01", environment="production",
        severity="critical", category="disk_space", description="Disk at 93% on /var of prod-db-01.",
    ))
    run = AgentRun(final_text=json.dumps({**GOOD, "incident_id": "INC1004"}), model="m",
                   tool_calls=[ToolCall("get_incident_details", {"incident_id_arg": "INC1004"})])
    diagnosis = make_service(repo, audit, incident_service, FixtureAgent(run)).diagnose("INC1004", mode="agent")
    assert diagnosis.policy_valid is False
    assert any("R5" in m for m in diagnosis.policy_messages)


def test_agent_using_unknown_tool_is_refused(repo, audit, incident_service):
    run = AgentRun(final_text=json.dumps(GOOD), model="m",
                   tool_calls=[ToolCall("run_shell", {"cmd": "rm -rf /"})], unexpected_tools=["run_shell"])
    with pytest.raises(LLMResponseError, match="non-allowlisted"):
        make_service(repo, audit, incident_service, FixtureAgent(run)).diagnose("INC1001", mode="agent")
    assert repo.get_latest_diagnosis("INC1001") is None


def test_agent_malformed_answer_raises_in_agent_mode(repo, audit, incident_service):
    run = AgentRun(final_text="The disk is probably full.", model="m")
    with pytest.raises(LLMResponseError):
        make_service(repo, audit, incident_service, FixtureAgent(run)).diagnose("INC1001", mode="agent")
    assert repo.get_incident("INC1001").status == IncidentStatus.OPEN


# --- auto mode: fallback ---------------------------------------------------------
def test_auto_mode_falls_back_to_direct_and_audits_it(repo, audit, incident_service):
    agent = FixtureAgent(error=LLMProviderError("agent service unavailable"))
    diagnosis = make_service(repo, audit, incident_service, agent).diagnose("INC1001", mode="auto")

    assert agent.calls == 1
    assert diagnosis.orchestration_mode == "direct_llm"
    failed = [e for e in repo.list_audit_events("INC1001") if e.action == AuditAction.DIAGNOSIS_FAILED]
    assert failed and failed[0].details["fallback"] == "direct_llm"


def test_auto_without_agent_uses_direct(repo, audit, incident_service):
    diagnosis = make_service(repo, audit, incident_service, agent=None).diagnose("INC1001", mode="auto")
    assert diagnosis.orchestration_mode == "direct_llm"


def test_unknown_mode_is_rejected(repo, audit, incident_service):
    with pytest.raises(ValueError):
        make_service(repo, audit, incident_service, None).diagnose("INC1001", mode="yolo")
