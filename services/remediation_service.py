"""Remediation service: workflow steps 10-13 (execute, verify, update, audit).

Execution gate (all must hold, checked in this order, every time):
  1. The execution exists and was APPROVED by a human.
  2. It has NOT already run (no double execution).
  3. The incident is in APPROVED status.
  4. Policy still allows the playbook on the target (re-checked right now).
Only then is the Ansible client called - with a playbook NAME and a host,
never a command.

Outcome rules ("a failed execution must not be reported as successful"):
  * Ansible FAILED              -> execution FAILED,              incident REMEDIATION_FAILED
  * Ansible timeout / exception -> execution TIMED_OUT / FAILED,  incident REMEDIATION_FAILED
  * Ansible ok, verify fails    -> execution VERIFICATION_FAILED, incident REMEDIATION_FAILED
  * Ansible ok, verify passes   -> execution SUCCEEDED,           incident RESOLVED
Verification reads the server's metrics from the repository AFTER the job,
i.e. it trusts monitoring data, not the job's own claim of success.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from core.exceptions import (
    ApprovalRequiredError,
    InvalidStateTransitionError,
    NotFoundError,
    PolicyViolationError,
)
from core.models import (
    ApprovalStatus,
    AuditAction,
    Category,
    ExecutionStatus,
    IncidentStatus,
    Metric,
    RemediationExecution,
)
from core.security import PolicyEngine
from database.repositories import OpsRepository
from integrations.ansible import AnsibleClient, AnsibleTimeoutError, JobOutcome
from integrations.servicenow import TicketClient
from services.audit_service import AuditService
from services.incident_service import IncidentService

ACTOR = "remediation-service"

# Verification thresholds per incident category: (metric, comparison, threshold).
VERIFICATION_RULES: dict[Category, tuple[str, str, float]] = {
    Category.DISK_SPACE: ("disk_used_pct", "<", 80.0),
    Category.HIGH_CPU: ("cpu_pct", "<", 70.0),
    Category.MEMORY: ("memory_pct", "<", 80.0),
    Category.SERVICE_FAILURE: ("service_up", "==", 1.0),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Snowflake may return naive or zone-aware timestamps; compare in UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class RemediationService:
    def __init__(self, repository: OpsRepository, incidents: IncidentService, policy: PolicyEngine,
                 ansible: AnsibleClient, audit: AuditService, tickets: Optional[TicketClient] = None,
                 timeout_seconds: int = 300) -> None:
        self.repository = repository
        self.incidents = incidents
        self.policy = policy
        self.ansible = ansible
        self.audit = audit
        self.tickets = tickets
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    def execute(self, execution_id: str, actor: str, ansible: Optional[AnsibleClient] = None) -> RemediationExecution:
        """Run an approved remediation. `ansible` overrides the client (used to pick a simulation scenario)."""
        client = ansible or self.ansible
        execution = self._check_gate(execution_id, actor)
        incident = self.incidents.get(execution.incident_id)

        execution.execution_status = ExecutionStatus.RUNNING
        self.repository.update_execution(execution)
        self.incidents.change_status(incident.incident_id, IncidentStatus.REMEDIATING, actor,
                                     reason=f"running {execution.playbook_name}")
        self.audit.record(AuditAction.EXECUTION_STARTED, actor, incident.incident_id,
                          execution_id=execution_id, playbook=execution.playbook_name,
                          target=execution.target_server, client=client.name)

        verification: Optional[dict[str, Any]] = None
        started_at = _now()
        try:
            job = client.run_playbook(
                execution.playbook_name, execution.target_server,
                extra_vars={"incident_id": incident.incident_id}, timeout_seconds=self.timeout_seconds,
            )
            execution.execution_output = job.stdout
            # Simulated monitoring: the server reports its new state after the job.
            for sample in job.post_state:
                self.repository.add_metric(Metric(
                    server_name=execution.target_server, metric_name=sample.metric_name,
                    metric_value=sample.value, mount_point=sample.mount_point, metric_timestamp=_now(),
                ))
            if job.outcome == JobOutcome.FAILED:
                execution.execution_status = ExecutionStatus.FAILED
            else:
                verification = self.verify(incident.category, execution.target_server,
                                           execution.playbook_name, started_at)
                execution.execution_status = (
                    ExecutionStatus.SUCCEEDED if verification["passed"] else ExecutionStatus.VERIFICATION_FAILED)
            job_details = {"job_id": job.job_id, "outcome": job.outcome.value,
                           "elapsed_seconds": round(job.elapsed_seconds, 2)}
        except AnsibleTimeoutError as error:
            execution.execution_status = ExecutionStatus.TIMED_OUT
            execution.execution_output = str(error)
            job_details = {"error": str(error)}
        except Exception as error:  # noqa: BLE001 - any client error is a failed execution, never success
            execution.execution_status = ExecutionStatus.FAILED
            execution.execution_output = f"{type(error).__name__}: {error}"
            job_details = {"error": execution.execution_output}

        execution.verification_result = verification
        execution.completed_at = _now()
        self.repository.update_execution(execution)
        self.audit.record(AuditAction.EXECUTION_FINISHED, client.name, incident.incident_id,
                          execution_id=execution_id, status=execution.execution_status.value, **job_details)
        if verification is not None:
            self.audit.record(
                AuditAction.VERIFICATION_PASSED if verification["passed"] else AuditAction.VERIFICATION_FAILED,
                ACTOR, incident.incident_id, execution_id=execution_id, **verification)

        resolved = execution.execution_status == ExecutionStatus.SUCCEEDED
        final_status = IncidentStatus.RESOLVED if resolved else IncidentStatus.REMEDIATION_FAILED
        self.incidents.change_status(incident.incident_id, final_status, ACTOR,
                                     reason=f"execution {execution.execution_status.value}")
        if resolved:
            self.audit.record(AuditAction.INCIDENT_RESOLVED, ACTOR, incident.incident_id,
                              execution_id=execution_id, verification=verification)
        self._update_ticket(incident.incident_id, execution)
        return self.repository.get_execution(execution_id)

    # ------------------------------------------------------------------
    def verify(self, category: Category, server_name: str, playbook_name: str,
               started_at: Optional[datetime] = None) -> dict[str, Any]:
        """Independent post-check from metrics. Returns a JSON-friendly result.

        `before` = last sample taken before the job started; `after` = latest sample
        taken after it started. No post-job sample means we cannot verify -> fail.
        """
        rule = VERIFICATION_RULES.get(category)
        if rule is None:
            return {"passed": False, "reason": f"no automatic verification rule for {category.value}; "
                                               "manual verification required"}
        metric_name, comparison, threshold = rule
        samples = sorted((m for m in self.repository.get_metrics(server_name) if m.metric_name == metric_name),
                         key=lambda m: m.metric_timestamp)
        started_at = started_at or datetime.min.replace(tzinfo=timezone.utc)
        prior = [m for m in samples if _as_utc(m.metric_timestamp) < started_at]
        post = [m for m in samples if _as_utc(m.metric_timestamp) >= started_at]
        if not post:
            return {"passed": False, "metric": metric_name, "rule": f"{metric_name} {comparison} {threshold}",
                    "before": prior[-1].metric_value if prior else None, "after": None,
                    "reason": "no metric reported after execution; cannot verify"}

        before = prior[-1].metric_value if prior else None
        after = post[-1].metric_value
        passed = after < threshold if comparison == "<" else after == threshold
        result = {"passed": passed, "metric": metric_name, "rule": f"{metric_name} {comparison} {threshold}",
                  "before": before, "after": after}
        if not passed and playbook_name.endswith("_diagnostics.yml"):
            result["reason"] = ("read-only diagnostics playbook collected data but did not change the server; "
                                "re-diagnose with the collected output")
        return result

    # ------------------------------------------------------------------
    def _check_gate(self, execution_id: str, actor: str) -> RemediationExecution:
        execution = self.repository.get_execution(execution_id)
        if execution is None:
            raise NotFoundError(f"execution {execution_id} not found")

        def blocked(reason: str, error_type: type[Exception]) -> Exception:
            self.audit.record(AuditAction.EXECUTION_BLOCKED, actor, execution.incident_id,
                              execution_id=execution_id, reason=reason)
            return error_type(reason)

        if execution.approval_status != ApprovalStatus.APPROVED:
            raise blocked(f"execution is {execution.approval_status.value}, not APPROVED", ApprovalRequiredError)
        if execution.execution_status != ExecutionStatus.NOT_STARTED:
            raise blocked(f"execution already {execution.execution_status.value}", InvalidStateTransitionError)

        incident = self.incidents.get(execution.incident_id)
        if incident.status != IncidentStatus.APPROVED:
            raise blocked(f"incident is {incident.status.value}, not APPROVED", InvalidStateTransitionError)

        decision = self.policy.validate_remediation(
            playbook_name=execution.playbook_name, server_name=execution.target_server,
            environment=incident.environment.value, category=incident.category.value,
        )
        if not decision.allowed:
            raise blocked("policy check failed at execution time: " + "; ".join(decision.messages),
                          PolicyViolationError)
        return execution

    def _update_ticket(self, incident_id: str, execution: RemediationExecution) -> None:
        if self.tickets is None:
            return
        state = "Resolved" if execution.execution_status == ExecutionStatus.SUCCEEDED else "In Progress"
        note = (f"OpsAgentX ran {execution.playbook_name} on {execution.target_server} "
                f"(approved by {execution.approved_by}). Result: {execution.execution_status.value}. "
                f"Verification: {execution.verification_result}")
        update = self.tickets.update_ticket(incident_id, state, note)
        self.audit.record(AuditAction.TICKET_UPDATED, self.tickets.name, incident_id,
                          state=update.state, work_note=update.work_note[:500])
