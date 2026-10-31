"""Stage 4 tests: LLM response validation, policy checks, diagnosis pipeline."""
import json

import pytest

from agent.tools import DiagnosticTools
from core.exceptions import InvalidStateTransitionError, LLMProviderError, LLMResponseError
from core.models import AuditAction, Incident, IncidentStatus
from core.security import PolicyEngine, contains_shell_command
from llm.base import LLMProvider, LLMResponse
from llm.schemas import diagnosis_json_schema, parse_diagnosis
from services.diagnosis_service import DiagnosisService
from services.knowledge_service import KeywordRunbookRetriever

GOOD = {
    "incident_id": "INC1001",
    "category": "disk_space",
    "severity": "high",
    "root_cause": "Disk utilization exceeded the threshold due to /tmp growth",
    "confidence": 0.92,
    "recommended_action": "Investigate temporary files and approved cleanup candidates",
    "recommended_playbook": "disk_cleanup.yml",
    "requires_human_approval": True,
    "evidence": ["Disk usage crossed 90%", "/tmp grew to 18.4 GB"],
}


class FakeLLM(LLMProvider):
    """Returns scripted text (or raises) so tests never call a real model."""

    name = "fake"

    def __init__(self, text: str = "", error: Exception | None = None):
        self.text, self.error, self.calls = text, error, []

    def generate_json(self, system_prompt, user_prompt, json_schema, temperature=0.0, max_tokens=1200):
        self.calls.append({"system": system_prompt, "user": user_prompt, "schema": json_schema})
        if self.error:
            raise self.error
        return LLMResponse(text=self.text, model="fake-model")


def make_service(repo, audit, incident_service, llm):
    policy = PolicyEngine(repo)
    tools = DiagnosticTools(repo, KeywordRunbookRetriever(repo), policy)
    return DiagnosisService(repo, incident_service, tools, policy, llm, audit)


# --- LLM response validation ------------------------------------------------
def test_valid_json_is_parsed():
    result = parse_diagnosis(json.dumps(GOOD))
    assert result.recommended_playbook == "disk_cleanup.yml"
    assert result.confidence == pytest.approx(0.92)


def test_json_inside_code_fence_and_prose_is_extracted():
    text = "Here is my analysis:\n```json\n" + json.dumps(GOOD) + "\n```\nHope this helps!"
    assert parse_diagnosis(text).incident_id == "INC1001"


@pytest.mark.parametrize("text", ["", "not json at all", "[1, 2, 3]", "{broken json"])
def test_malformed_response_raises(text):
    with pytest.raises(LLMResponseError):
        parse_diagnosis(text)


@pytest.mark.parametrize("missing", ["root_cause", "confidence", "recommended_playbook", "requires_human_approval"])
def test_missing_diagnosis_fields_raise(missing):
    data = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(LLMResponseError, match=missing):
        parse_diagnosis(json.dumps(data))


@pytest.mark.parametrize(
    "field, value",
    [
        ("confidence", 1.7),
        ("confidence", "very high"),
        ("category", "printer_jam"),
        ("recommended_playbook", "rm -rf /tmp/*"),          # a command, not a playbook name
        ("recommended_playbook", "../../etc/passwd.yml"),
    ],
)
def test_invalid_field_values_raise(field, value):
    with pytest.raises(LLMResponseError):
        parse_diagnosis(json.dumps({**GOOD, field: value}))


def test_schema_restricts_playbook_to_allowlist():
    schema = diagnosis_json_schema(["disk_cleanup.yml"])
    assert schema["properties"]["recommended_playbook"]["enum"] == ["disk_cleanup.yml", "none"]


# --- policy engine ----------------------------------------------------------
def test_unknown_playbook_is_rejected(repo):
    decision = PolicyEngine(repo).validate_remediation("format_disk.yml", "server-01", "development", "disk_space")
    assert not decision.allowed
    assert any("R1" in m for m in decision.messages)


def test_playbook_for_wrong_category_is_rejected(repo):
    decision = PolicyEngine(repo).validate_remediation("restart_service.yml", "server-01", "development", "disk_space")
    assert not decision.allowed and any("R2" in m for m in decision.messages)


def test_playbook_not_allowed_in_environment_is_rejected(repo):
    decision = PolicyEngine(repo).validate_remediation(
        "restart_high_cpu_process.yml", "prod-app-01", "production", "high_cpu")
    assert not decision.allowed and any("R3" in m for m in decision.messages)


def test_unknown_server_is_rejected(repo):
    decision = PolicyEngine(repo).validate_remediation("disk_cleanup.yml", "rogue-host", "development", "disk_space")
    assert not decision.allowed and any("R4" in m for m in decision.messages)


def test_server_without_automation_is_rejected(repo):
    decision = PolicyEngine(repo).validate_remediation("disk_cleanup.yml", "prod-db-01", "production", "disk_space")
    assert not decision.allowed and any("R5" in m for m in decision.messages)


def test_environment_spoofing_is_rejected(repo):
    # Incident claims development, but prod-app-01 is production in the inventory.
    decision = PolicyEngine(repo).validate_remediation("disk_cleanup.yml", "prod-app-01", "development", "disk_space")
    assert not decision.allowed and any("R6" in m for m in decision.messages)


def test_valid_remediation_still_requires_approval(repo):
    decision = PolicyEngine(repo).validate_remediation("disk_cleanup.yml", "server-01", "development", "disk_space")
    assert decision.allowed and decision.requires_approval


@pytest.mark.parametrize("text, expected", [
    ("Clean approved temp files older than 7 days", False),
    ("Run sudo rm -rf /var/lib/app", True),
    ("restart && reboot", True),
])
def test_shell_command_detection(text, expected):
    assert contains_shell_command(text) is expected


# --- diagnosis pipeline -----------------------------------------------------
def test_successful_diagnosis_is_stored_audited_and_marks_incident(repo, audit, incident_service):
    llm = FakeLLM(json.dumps(GOOD))
    diagnosis = make_service(repo, audit, incident_service, llm).diagnose("INC1001")

    assert diagnosis.policy_valid
    assert diagnosis.recommended_playbook == "disk_cleanup.yml"
    assert "analyze_disk_usage" in diagnosis.tools_used          # disk-specific tool chosen
    assert diagnosis.runbooks_used[0] == "RB-001"                # disk runbook ranked first
    assert "RB-900" not in diagnosis.runbooks_used               # draft runbook never used
    assert repo.get_latest_diagnosis("INC1001").diagnosis_id == diagnosis.diagnosis_id
    assert repo.get_incident("INC1001").status == IncidentStatus.DIAGNOSED

    actions = [e.action for e in repo.list_audit_events("INC1001")]
    for expected in (AuditAction.DIAGNOSIS_REQUESTED, AuditAction.TOOL_CALLED, AuditAction.RUNBOOKS_RETRIEVED,
                     AuditAction.DIAGNOSIS_COMPLETED, AuditAction.POLICY_VALIDATED, AuditAction.STATUS_CHANGED):
        assert expected in actions

    # The prompt included the logs and the allowlist; the model saw no draft runbook.
    prompt = llm.calls[0]["user"]
    assert "Disk usage crossed 90%" in prompt and "disk_cleanup.yml" in prompt
    assert "rm -rf everything" not in prompt


def test_model_cannot_waive_approval_or_relabel_incident(repo, audit, incident_service):
    sneaky = {**GOOD, "incident_id": "INC0000", "requires_human_approval": False}
    diagnosis = make_service(repo, audit, incident_service, FakeLLM(json.dumps(sneaky))).diagnose("INC1001")
    assert diagnosis.incident_id == "INC1001"
    assert diagnosis.requires_human_approval is True
    assert any("overridden" in m for m in diagnosis.policy_messages)


def test_invented_but_well_formed_playbook_fails_policy(repo, audit, incident_service):
    invented = {**GOOD, "recommended_playbook": "wipe_everything.yml"}
    diagnosis = make_service(repo, audit, incident_service, FakeLLM(json.dumps(invented))).diagnose("INC1001")
    assert diagnosis.policy_valid is False
    actions = [e.action for e in repo.list_audit_events("INC1001")]
    assert AuditAction.POLICY_REJECTED in actions


def test_malformed_model_output_is_audited_and_not_stored(repo, audit, incident_service):
    service = make_service(repo, audit, incident_service, FakeLLM("I think the disk is full."))
    with pytest.raises(LLMResponseError):
        service.diagnose("INC1001")
    assert repo.get_latest_diagnosis("INC1001") is None
    assert repo.get_incident("INC1001").status == IncidentStatus.OPEN
    assert repo.list_audit_events("INC1001")[-1].action == AuditAction.DIAGNOSIS_FAILED


def test_model_error_is_audited(repo, audit, incident_service):
    service = make_service(repo, audit, incident_service, FakeLLM(error=LLMProviderError("quota exceeded")))
    with pytest.raises(LLMProviderError):
        service.diagnose("INC1001")
    failed = repo.list_audit_events("INC1001")[-1]
    assert failed.action == AuditAction.DIAGNOSIS_FAILED and "quota" in failed.details["error"]


def test_production_db_incident_is_blocked_by_policy(repo, audit, incident_service):
    repo.insert_incident(Incident(
        incident_id="INC1004", source="monitoring", server_name="prod-db-01", environment="production",
        severity="critical", category="disk_space", description="Disk at 93% on /var of prod-db-01.",
    ))
    diagnosis = make_service(repo, audit, incident_service,
                             FakeLLM(json.dumps({**GOOD, "incident_id": "INC1004"}))).diagnose("INC1004")
    assert diagnosis.policy_valid is False
    assert any("R5" in m for m in diagnosis.policy_messages)


def test_resolved_incident_cannot_be_diagnosed(repo, audit, incident_service):
    with pytest.raises(InvalidStateTransitionError):
        make_service(repo, audit, incident_service, FakeLLM(json.dumps(GOOD))).diagnose("INC0901")
