"""Stage 9: end-to-end lifecycle of all three demo scenarios on synthetic data.

  INC1001  disk 95% on server-01 (development)   -> disk_cleanup.yml        -> RESOLVED
  INC1002  CPU 96% on server-02 (test)            -> cpu_diagnostics.yml     -> REMEDIATION_FAILED
           (read-only playbook; verification correctly refuses to call it fixed)
  INC1003  nginx stopped on server-03 (development) -> restart_service.yml   -> RESOLVED
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from core.models import AuditAction, ExecutionStatus, Incident, IncidentStatus, Metric
from tests.test_diagnosis import GOOD
from tests.test_remediation import Platform

NOW = datetime.now(timezone.utc)

SCENARIOS = {
    "INC1001": {
        "incident": dict(source="monitoring", server_name="server-01", environment="development",
                         severity="high", category="disk_space",
                         description="Disk utilization reached 95% on / of server-01."),
        "answer": GOOD,
        "expected_execution": ExecutionStatus.SUCCEEDED,
        "expected_status": IncidentStatus.RESOLVED,
    },
    "INC1002": {
        "incident": dict(source="monitoring", server_name="server-02", environment="test",
                         severity="high", category="high_cpu",
                         description="CPU utilization above 90% for 25 minutes on server-02."),
        "metrics": [Metric(server_name="server-02", metric_name="cpu_pct", metric_value=96.5,
                           metric_timestamp=NOW - timedelta(minutes=45))],
        "answer": {**GOOD, "incident_id": "INC1002", "category": "high_cpu",
                   "root_cause": "batch-worker stuck in a retry loop after deploy",
                   "recommended_playbook": "cpu_diagnostics.yml"},
        "expected_execution": ExecutionStatus.VERIFICATION_FAILED,
        "expected_status": IncidentStatus.REMEDIATION_FAILED,
    },
    "INC1003": {
        "incident": dict(source="servicenow", server_name="server-03", environment="development",
                         severity="critical", category="service_failure",
                         description="nginx service stopped unexpectedly on server-03; health check failing."),
        "metrics": [Metric(server_name="server-03", metric_name="service_up", metric_value=0.0,
                           mount_point="nginx", metric_timestamp=NOW - timedelta(minutes=15))],
        "answer": {**GOOD, "incident_id": "INC1003", "category": "service_failure", "severity": "critical",
                   "root_cause": "nginx exited after a configuration reload",
                   "recommended_playbook": "restart_service.yml"},
        "expected_execution": ExecutionStatus.SUCCEEDED,
        "expected_status": IncidentStatus.RESOLVED,
    },
}


@pytest.mark.parametrize("incident_id", list(SCENARIOS))
def test_demo_scenario_lifecycle(incident_id, repo, audit, incident_service):
    scenario = SCENARIOS[incident_id]
    if incident_id != "INC1001":  # INC1001 is already seeded by conftest
        incident_service.ingest({"incident_id": incident_id, **scenario["incident"]}, actor="monitoring-feed")
    for metric in scenario.get("metrics", []):
        repo.add_metric(metric)

    platform = Platform(repo, audit, incident_service, llm_answer=scenario["answer"])
    diagnosis = platform.diagnosis.diagnose(incident_id, actor="operator")
    assert diagnosis.policy_valid, diagnosis.policy_messages

    execution = platform.approvals.request_approval(incident_id, requested_by="operator")
    execution = platform.approvals.approve(execution.execution_id, "operator", "demo", confirmed=True)
    result = platform.remediation.execute(execution.execution_id, actor="operator")

    assert result.execution_status == scenario["expected_execution"]
    assert repo.get_incident(incident_id).status == scenario["expected_status"]

    actions = [e.action for e in repo.list_audit_events(incident_id)]
    assert AuditAction.APPROVAL_GRANTED in actions and AuditAction.EXECUTION_FINISHED in actions
    assert (AuditAction.INCIDENT_RESOLVED in actions) == (scenario["expected_status"] == IncidentStatus.RESOLVED)
    # Every audit row is JSON-serializable (it is stored as a Snowflake VARIANT).
    for event in repo.list_audit_events(incident_id):
        json.dumps(event.details, default=str)


def test_production_db_scenario_never_reaches_approval(repo, audit, incident_service):
    repo.insert_incident(Incident(
        incident_id="INC1004", source="monitoring", server_name="prod-db-01", environment="production",
        severity="critical", category="disk_space", description="Disk at 93% on /var of prod-db-01.",
    ))
    platform = Platform(repo, audit, incident_service, llm_answer={**GOOD, "incident_id": "INC1004"})
    diagnosis = platform.diagnosis.diagnose("INC1004")
    assert not diagnosis.policy_valid
    with pytest.raises(Exception):
        platform.approvals.request_approval("INC1004", "operator")
    assert repo.list_executions("INC1004") == []
    assert platform.ansible.calls == []
