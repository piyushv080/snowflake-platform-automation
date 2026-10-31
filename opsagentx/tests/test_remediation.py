"""Stage 7 tests: approval gate, simulated Ansible, verification, lifecycle, audit."""
import json
from datetime import datetime, timezone

import pytest

from agent.tools import DiagnosticTools
from core.exceptions import (
    ApprovalRequiredError,
    InvalidStateTransitionError,
    PolicyViolationError,
)
from core.models import ApprovalStatus, AuditAction, Category, ExecutionStatus, IncidentStatus
from core.security import PolicyEngine
from integrations.ansible import SimulatedAnsibleClient, SimulationScenario
from integrations.servicenow import SimulatedServiceNowClient
from services.approval_service import ApprovalService
from services.diagnosis_service import DiagnosisService
from services.knowledge_service import KeywordRunbookRetriever
from services.remediation_service import RemediationService
from tests.test_diagnosis import GOOD, FakeLLM


class Platform:
    """All services wired against the in-memory repository."""

    def __init__(self, repo, audit, incident_service, llm_answer=GOOD):
        self.repo = repo
        self.policy = PolicyEngine(repo)
        tools = DiagnosticTools(repo, KeywordRunbookRetriever(repo), self.policy)
        self.diagnosis = DiagnosisService(repo, incident_service, tools, self.policy,
                                          FakeLLM(json.dumps(llm_answer)), audit)
        self.approvals = ApprovalService(repo, incident_service, self.policy, audit)
        self.ansible = SimulatedAnsibleClient(simulated_duration_seconds=0)
        self.tickets = SimulatedServiceNowClient()
        self.remediation = RemediationService(repo, incident_service, self.policy, self.ansible, audit,
                                              tickets=self.tickets)

    def pending(self, incident_id="INC1001"):
        self.diagnosis.diagnose(incident_id)
        return self.approvals.request_approval(incident_id, requested_by="alice")

    def approved(self, incident_id="INC1001"):
        execution = self.pending(incident_id)
        return self.approvals.approve(execution.execution_id, "alice", "looks safe", confirmed=True)


def sim(scenario):
    return SimulatedAnsibleClient(scenario, simulated_duration_seconds=0)


@pytest.fixture
def platform(repo, audit, incident_service):
    return Platform(repo, audit, incident_service)


def actions(repo, incident_id="INC1001"):
    return [e.action for e in repo.list_audit_events(incident_id)]


# --- approval request ---------------------------------------------------------
def test_request_creates_pending_execution_from_diagnosis(platform, repo):
    execution = platform.pending()
    assert execution.approval_status == ApprovalStatus.PENDING
    assert execution.execution_status == ExecutionStatus.NOT_STARTED
    assert execution.playbook_name == "disk_cleanup.yml"
    assert execution.target_server == "server-01"
    assert repo.get_incident("INC1001").status == IncidentStatus.PENDING_APPROVAL
    assert AuditAction.APPROVAL_REQUESTED in actions(repo)


def test_request_refused_when_diagnosis_failed_policy(repo, audit, incident_service):
    platform = Platform(repo, audit, incident_service, {**GOOD, "recommended_playbook": "wipe_disk.yml"})
    platform.diagnosis.diagnose("INC1001")
    with pytest.raises(PolicyViolationError):
        platform.approvals.request_approval("INC1001", "alice")
    assert repo.list_executions("INC1001") == []


def test_request_refused_when_no_playbook(repo, audit, incident_service):
    platform = Platform(repo, audit, incident_service, {**GOOD, "recommended_playbook": "none"})
    platform.diagnosis.diagnose("INC1001")
    with pytest.raises(PolicyViolationError, match="no playbook"):
        platform.approvals.request_approval("INC1001", "alice")


def test_request_requires_diagnosis_first(platform):
    with pytest.raises(InvalidStateTransitionError):
        platform.approvals.request_approval("INC1001", "alice")


# --- approval decision ----------------------------------------------------------
def test_approval_requires_explicit_confirmation(platform):
    execution = platform.pending()
    with pytest.raises(ApprovalRequiredError, match="explicitly confirmed"):
        platform.approvals.approve(execution.execution_id, "alice", confirmed=False)


@pytest.mark.parametrize("bot", ["opsagentx-agent", "policy-engine", "llm", ""])
def test_automated_actors_cannot_approve(platform, bot):
    execution = platform.pending()
    with pytest.raises(ApprovalRequiredError, match="not a human"):
        platform.approvals.approve(execution.execution_id, bot, confirmed=True)


def test_approval_is_recorded(platform, repo):
    execution = platform.approved()
    assert execution.approval_status == ApprovalStatus.APPROVED
    assert execution.approved_by == "alice" and execution.approved_at is not None
    assert repo.get_incident("INC1001").status == IncidentStatus.APPROVED
    assert AuditAction.APPROVAL_GRANTED in actions(repo)


def test_policy_change_between_request_and_approval_blocks(platform, repo):
    execution = platform.pending()
    repo.servers["server-01"] = repo.servers["server-01"].model_copy(update={"allow_automation": False})
    with pytest.raises(PolicyViolationError, match="R5"):
        platform.approvals.approve(execution.execution_id, "alice", confirmed=True)
    closed = repo.get_execution(execution.execution_id)
    assert closed.execution_status == ExecutionStatus.CANCELLED
    assert repo.get_incident("INC1001").status == IncidentStatus.REJECTED


# --- rejection ------------------------------------------------------------------
def test_rejection_requires_reason(platform):
    execution = platform.pending()
    with pytest.raises(ApprovalRequiredError, match="reason"):
        platform.approvals.reject(execution.execution_id, "bob", reason="")


def test_rejected_action_never_executes(platform, repo):
    execution = platform.pending()
    platform.approvals.reject(execution.execution_id, "bob", reason="change freeze tonight")

    with pytest.raises(ApprovalRequiredError):
        platform.remediation.execute(execution.execution_id, actor="bob")
    assert platform.ansible.calls == []                         # Ansible never called
    stored = repo.get_execution(execution.execution_id)
    assert stored.approval_status == ApprovalStatus.REJECTED
    assert stored.execution_status == ExecutionStatus.CANCELLED
    assert repo.get_incident("INC1001").status == IncidentStatus.REJECTED
    assert AuditAction.APPROVAL_DENIED in actions(repo)
    assert AuditAction.EXECUTION_BLOCKED in actions(repo)


def test_cannot_approve_twice_or_after_rejection(platform):
    execution = platform.pending()
    platform.approvals.reject(execution.execution_id, "bob", reason="not now")
    with pytest.raises(InvalidStateTransitionError):
        platform.approvals.approve(execution.execution_id, "alice", confirmed=True)


def test_rejected_incident_can_be_rediagnosed(platform, repo):
    execution = platform.pending()
    platform.approvals.reject(execution.execution_id, "bob", reason="try another approach")
    platform.diagnosis.diagnose("INC1001")
    assert repo.get_incident("INC1001").status == IncidentStatus.DIAGNOSED


# --- execution gate -----------------------------------------------------------------
def test_pending_execution_cannot_run(platform):
    execution = platform.pending()
    with pytest.raises(ApprovalRequiredError):
        platform.remediation.execute(execution.execution_id, actor="alice")
    assert platform.ansible.calls == []


def test_execution_rechecks_policy(platform, repo):
    execution = platform.approved()
    repo.playbooks = [p for p in repo.playbooks if p.playbook_name != "disk_cleanup.yml"]  # removed from allowlist
    with pytest.raises(PolicyViolationError, match="R1"):
        platform.remediation.execute(execution.execution_id, actor="alice")
    assert platform.ansible.calls == []


def test_execution_cannot_run_twice(platform):
    execution = platform.approved()
    platform.remediation.execute(execution.execution_id, actor="alice")
    with pytest.raises(InvalidStateTransitionError):
        platform.remediation.execute(execution.execution_id, actor="alice")
    assert len(platform.ansible.calls) == 1


# --- simulated outcomes ---------------------------------------------------------------
def test_successful_remediation_resolves_incident(platform, repo):
    execution = platform.approved()
    result = platform.remediation.execute(execution.execution_id, actor="alice")

    assert result.execution_status == ExecutionStatus.SUCCEEDED
    assert result.verification_result["passed"] is True
    assert result.verification_result["before"] == 95.0 and result.verification_result["after"] == 71.5
    assert "PLAY RECAP" in result.execution_output and "failed=0" in result.execution_output
    assert repo.get_incident("INC1001").status == IncidentStatus.RESOLVED
    assert platform.ansible.calls[0] == {"playbook": "disk_cleanup.yml", "target": "server-01",
                                         "extra_vars": {"incident_id": "INC1001"}}
    for expected in (AuditAction.EXECUTION_STARTED, AuditAction.EXECUTION_FINISHED,
                     AuditAction.VERIFICATION_PASSED, AuditAction.INCIDENT_RESOLVED, AuditAction.TICKET_UPDATED):
        assert expected in actions(repo)
    assert platform.tickets.updates[-1].state == "Resolved"


def test_failed_remediation_is_not_reported_as_success(platform, repo):
    execution = platform.approved()
    result = platform.remediation.execute(execution.execution_id, "alice", ansible=sim(SimulationScenario.FAILURE))

    assert result.execution_status == ExecutionStatus.FAILED
    assert "FAILED!" in result.execution_output
    assert result.verification_result is None                    # never verified a failed job
    assert repo.get_incident("INC1001").status == IncidentStatus.REMEDIATION_FAILED
    assert AuditAction.INCIDENT_RESOLVED not in actions(repo)
    assert platform.tickets.updates[-1].state == "In Progress"


def test_timeout_is_recorded_as_timed_out(platform, repo):
    execution = platform.approved()
    result = platform.remediation.execute(execution.execution_id, "alice", ansible=sim(SimulationScenario.TIMEOUT))
    assert result.execution_status == ExecutionStatus.TIMED_OUT
    assert "within" in result.execution_output
    assert repo.get_incident("INC1001").status == IncidentStatus.REMEDIATION_FAILED


def test_verification_failure_keeps_incident_unresolved(platform, repo):
    execution = platform.approved()
    result = platform.remediation.execute(
        execution.execution_id, "alice", ansible=sim(SimulationScenario.VERIFICATION_FAILURE))

    assert result.execution_status == ExecutionStatus.VERIFICATION_FAILED
    assert "failed=0" in result.execution_output                 # Ansible itself claimed success...
    assert result.verification_result["passed"] is False         # ...but disk is still 93.8%
    assert result.verification_result["after"] == 93.8
    assert repo.get_incident("INC1001").status == IncidentStatus.REMEDIATION_FAILED
    assert AuditAction.VERIFICATION_FAILED in actions(repo)


def test_client_crash_is_a_failure(platform, repo):
    class Exploding(SimulatedAnsibleClient):
        def run_playbook(self, *args, **kwargs):
            raise ConnectionError("controller unreachable")

    execution = platform.approved()
    result = platform.remediation.execute(execution.execution_id, "alice", ansible=Exploding())
    assert result.execution_status == ExecutionStatus.FAILED
    assert "ConnectionError" in result.execution_output
    assert repo.get_incident("INC1001").status == IncidentStatus.REMEDIATION_FAILED


def test_failed_remediation_can_be_retried_from_diagnosis(platform, repo):
    execution = platform.approved()
    platform.remediation.execute(execution.execution_id, "alice", ansible=sim(SimulationScenario.FAILURE))
    second = platform.approved()                                  # re-diagnose -> request -> approve
    result = platform.remediation.execute(second.execution_id, "alice")
    assert result.execution_status == ExecutionStatus.SUCCEEDED
    assert len(repo.list_executions("INC1001")) == 2


def test_simulator_refuses_unknown_playbooks():
    with pytest.raises(ValueError):
        SimulatedAnsibleClient(simulated_duration_seconds=0).run_playbook("rm -rf /", "server-01", {})


# --- full lifecycle audit trail ---------------------------------------------------------
def test_complete_lifecycle_audit_trail(platform, repo):
    execution = platform.approved()
    platform.remediation.execute(execution.execution_id, actor="alice")

    trail = actions(repo)
    expected_order = [
        AuditAction.DIAGNOSIS_REQUESTED, AuditAction.RUNBOOKS_RETRIEVED, AuditAction.DIAGNOSIS_COMPLETED,
        AuditAction.POLICY_VALIDATED, AuditAction.APPROVAL_REQUESTED, AuditAction.APPROVAL_GRANTED,
        AuditAction.EXECUTION_STARTED, AuditAction.EXECUTION_FINISHED, AuditAction.VERIFICATION_PASSED,
        AuditAction.INCIDENT_RESOLVED, AuditAction.TICKET_UPDATED,
    ]
    positions = [trail.index(action) for action in expected_order]
    assert positions == sorted(positions), "audit events out of lifecycle order"

    statuses = [e.details["to_status"] for e in repo.list_audit_events("INC1001")
                if e.action == AuditAction.STATUS_CHANGED]
    assert statuses == ["DIAGNOSED", "PENDING_APPROVAL", "APPROVED", "REMEDIATING", "RESOLVED"]


def test_verification_without_new_metric_fails(platform):
    # No sample taken after "now" exists, so verification must not pass on stale data.
    result = platform.remediation.verify(Category.DISK_SPACE, "server-01", "disk_cleanup.yml",
                                         started_at=datetime.now(timezone.utc))
    assert result["passed"] is False and "cannot verify" in result["reason"]


def test_verification_baseline_is_last_sample_before_job(platform):
    execution = platform.approved()
    result = platform.remediation.execute(execution.execution_id, actor="alice")
    assert result.verification_result["before"] == 95.0   # the 95% sample, not the oldest one
    assert result.verification_result["after"] == 71.5
