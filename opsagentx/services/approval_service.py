"""Approval service: workflow step 9 (human approval).

Rules:
* A remediation request is created ONLY from a stored diagnosis whose
  playbook passed policy. Its playbook and target are copied from the
  diagnosis / incident and can never be edited afterwards.
* Policy is re-checked when the request is created AND when it is approved
  (the inventory or allowlist may have changed in between).
* Only a named human may approve or reject. Automated actors (the agent,
  the diagnosis service, the simulator, ...) are refused.
* The approver must confirm explicitly (`confirmed=True`), e.g. a checkbox
  in the UI. Rejection requires a reason.
* A rejected request is closed (CANCELLED) and can never be executed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.exceptions import (
    ApprovalRequiredError,
    InvalidStateTransitionError,
    NotFoundError,
    PolicyViolationError,
)
from core.models import (
    ApprovalStatus,
    AuditAction,
    ExecutionStatus,
    IncidentStatus,
    RemediationExecution,
)
from core.security import PolicyEngine
from database.repositories import OpsRepository
from llm.schemas import NO_PLAYBOOK
from services.audit_service import AuditService
from services.incident_service import IncidentService

# Identities that represent software, not people. They can never approve.
AUTOMATED_ACTORS = {
    "opsagentx-agent", "opsagentx-diagnosis", "policy-engine", "ansible-sim",
    "remediation-service", "ingestion-api", "sample-data-loader", "system", "llm", "",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalService:
    def __init__(self, repository: OpsRepository, incidents: IncidentService,
                 policy: PolicyEngine, audit: AuditService) -> None:
        self.repository = repository
        self.incidents = incidents
        self.policy = policy
        self.audit = audit

    # ------------------------------------------------------------------
    def request_approval(self, incident_id: str, requested_by: str) -> RemediationExecution:
        """Turn the latest valid diagnosis into a PENDING approval request."""
        incident = self.incidents.get(incident_id)
        if incident.status != IncidentStatus.DIAGNOSED:
            raise InvalidStateTransitionError(
                f"{incident_id} is {incident.status.value}; approval can only be requested after diagnosis")

        diagnosis = self.repository.get_latest_diagnosis(incident_id)
        if diagnosis is None:
            raise NotFoundError(f"{incident_id} has no diagnosis")
        if not diagnosis.recommended_playbook or diagnosis.recommended_playbook == NO_PLAYBOOK:
            raise PolicyViolationError(f"{incident_id}: diagnosis recommends no playbook; nothing to approve")

        decision = self.policy.validate_remediation(
            playbook_name=diagnosis.recommended_playbook, server_name=incident.server_name,
            environment=incident.environment.value, category=incident.category.value,
            recommended_action=diagnosis.recommended_action,
        )
        if not decision.allowed:
            self.audit.record(AuditAction.POLICY_REJECTED, "policy-engine", incident_id,
                              stage="request_approval", messages=decision.messages)
            raise PolicyViolationError("; ".join(decision.messages))

        execution = RemediationExecution(
            incident_id=incident_id,
            diagnosis_id=diagnosis.diagnosis_id,
            playbook_name=diagnosis.recommended_playbook,   # from the diagnosis, never from user input
            target_server=incident.server_name,            # from the incident, never from the LLM
        )
        execution_id = self.repository.create_execution(execution)
        self.audit.record(
            AuditAction.APPROVAL_REQUESTED, requested_by, incident_id, execution_id=execution_id,
            playbook=execution.playbook_name, target=execution.target_server,
            environment=incident.environment.value, policy=decision.to_dict(),
        )
        self.incidents.change_status(incident_id, IncidentStatus.PENDING_APPROVAL, requested_by,
                                     reason=f"approval requested for {execution.playbook_name}")
        return self.repository.get_execution(execution_id)

    # ------------------------------------------------------------------
    def approve(self, execution_id: str, approver: str, comment: str = "", confirmed: bool = False) -> RemediationExecution:
        execution = self._pending(execution_id)
        approver = self._check_human(approver)
        if not confirmed:
            raise ApprovalRequiredError("approval must be explicitly confirmed by the approver")

        incident = self.incidents.get(execution.incident_id)
        decision = self.policy.validate_remediation(
            playbook_name=execution.playbook_name, server_name=execution.target_server,
            environment=incident.environment.value, category=incident.category.value,
        )
        if not decision.allowed:
            # Policy changed since the request: block, record, and close the request.
            self.audit.record(AuditAction.POLICY_REJECTED, "policy-engine", execution.incident_id,
                              stage="approve", execution_id=execution_id, messages=decision.messages)
            self._close(execution, approver, "auto-closed: policy check failed at approval time")
            self.incidents.change_status(execution.incident_id, IncidentStatus.REJECTED, "policy-engine",
                                         reason="policy check failed at approval time")
            raise PolicyViolationError("; ".join(decision.messages))

        execution.approval_status = ApprovalStatus.APPROVED
        execution.approved_by = approver
        execution.approval_comment = comment.strip()[:1000] or None
        execution.approved_at = _now()
        self.repository.update_execution(execution)
        self.audit.record(AuditAction.APPROVAL_GRANTED, approver, execution.incident_id,
                          execution_id=execution_id, playbook=execution.playbook_name,
                          target=execution.target_server, comment=execution.approval_comment)
        self.incidents.change_status(execution.incident_id, IncidentStatus.APPROVED, approver,
                                     reason=f"approved {execution.playbook_name}")
        return self.repository.get_execution(execution_id)

    def reject(self, execution_id: str, approver: str, reason: str) -> RemediationExecution:
        execution = self._pending(execution_id)
        approver = self._check_human(approver)
        if not reason or len(reason.strip()) < 3:
            raise ApprovalRequiredError("a rejection reason is required")

        self._close(execution, approver, reason.strip()[:1000])
        self.audit.record(AuditAction.APPROVAL_DENIED, approver, execution.incident_id,
                          execution_id=execution_id, playbook=execution.playbook_name, reason=reason.strip())
        self.incidents.change_status(execution.incident_id, IncidentStatus.REJECTED, approver,
                                     reason=f"rejected: {reason.strip()[:200]}")
        return self.repository.get_execution(execution_id)

    def history(self, incident_id: str | None = None) -> list[RemediationExecution]:
        return self.repository.list_executions(incident_id)

    # ------------------------------------------------------------------
    def _pending(self, execution_id: str) -> RemediationExecution:
        execution = self.repository.get_execution(execution_id)
        if execution is None:
            raise NotFoundError(f"execution {execution_id} not found")
        if execution.approval_status != ApprovalStatus.PENDING:
            raise InvalidStateTransitionError(
                f"execution {execution_id} is already {execution.approval_status.value}")
        return execution

    @staticmethod
    def _check_human(approver: str) -> str:
        approver = (approver or "").strip()
        if approver.lower() in AUTOMATED_ACTORS:
            raise ApprovalRequiredError(f"'{approver or '<empty>'}' is not a human approver")
        return approver

    def _close(self, execution: RemediationExecution, approver: str, comment: str) -> None:
        execution.approval_status = ApprovalStatus.REJECTED
        execution.approved_by = approver
        execution.approval_comment = comment
        execution.approved_at = _now()
        execution.execution_status = ExecutionStatus.CANCELLED
        execution.completed_at = _now()
        self.repository.update_execution(execution)
