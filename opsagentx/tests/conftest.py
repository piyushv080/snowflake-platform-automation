"""Shared pytest fixtures.

Tests run entirely in memory: no Snowflake connection, no LLM calls, no cost.
The seed data mirrors sql/02_sample_data.sql closely enough for the demo flows.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# Make the project root importable (tests/ sits one level below it).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.models import (  # noqa: E402
    Incident,
    IncidentStatus,
    LogEntry,
    Metric,
    Playbook,
    Runbook,
    Server,
)
from database.memory_repository import InMemoryRepository  # noqa: E402
from services.audit_service import AuditService  # noqa: E402
from services.incident_service import IncidentService  # noqa: E402

NOW = datetime.now(timezone.utc)


def _seed(repo: InMemoryRepository) -> None:
    repo.servers = {
        s.server_name: s
        for s in [
            Server(server_name="server-01", environment="development", allow_automation=True),
            Server(server_name="server-02", environment="test", allow_automation=True),
            Server(server_name="server-03", environment="development", allow_automation=True),
            Server(server_name="prod-app-01", environment="production", allow_automation=True),
            Server(server_name="prod-db-01", environment="production", allow_automation=False),
        ]
    }
    repo.playbooks = [
        Playbook(playbook_name="disk_cleanup.yml", category="disk_space",
                 allowed_environments=["development", "test", "production"], risk_level="low"),
        Playbook(playbook_name="restart_service.yml", category="service_failure",
                 allowed_environments=["development", "test", "production"], risk_level="medium"),
        Playbook(playbook_name="cpu_diagnostics.yml", category="high_cpu",
                 allowed_environments=["development", "test", "production"], risk_level="low"),
        Playbook(playbook_name="restart_high_cpu_process.yml", category="high_cpu",
                 allowed_environments=["development", "test"], risk_level="medium"),
    ]
    repo.runbooks = [
        Runbook(runbook_id="RB-001", title="Linux Disk Space Troubleshooting", category="disk_space",
                content="Check /tmp growth. Approved remediation: disk_cleanup.yml.", approved=True),
        Runbook(runbook_id="RB-002", title="High CPU Utilization Investigation", category="high_cpu",
                content="Run cpu_diagnostics.yml first.", approved=True),
        Runbook(runbook_id="RB-004", title="Linux Service Failure Investigation", category="service_failure",
                content="Run restart_service.yml for approved services.", approved=True),
        Runbook(runbook_id="RB-900", title="DRAFT - Aggressive disk purge", category="disk_space",
                content="rm -rf everything", approved=False),
    ]
    repo.insert_incident(Incident(
        incident_id="INC1001", source="monitoring", server_name="server-01", environment="development",
        severity="high", category="disk_space", description="Disk utilization reached 95% on / of server-01.",
    ))
    repo.insert_incident(Incident(
        incident_id="INC0901", source="monitoring", server_name="server-01", environment="development",
        severity="medium", category="disk_space", status=IncidentStatus.RESOLVED,
        description="Disk at 88%; /tmp growth from report exports.",
    ))
    repo.logs = [
        LogEntry(server_name="server-01", log_level="WARN", log_message="Disk usage crossed 90% on / (91.2%).",
                 log_timestamp=NOW - timedelta(minutes=40), incident_id="INC1001"),
        LogEntry(server_name="server-01", log_level="WARN", log_message="Temporary directory usage increased.",
                 log_timestamp=NOW - timedelta(minutes=35), incident_id="INC1001"),
        LogEntry(server_name="server-01", log_level="INFO", log_message="Application service is still running.",
                 log_timestamp=NOW - timedelta(minutes=32), incident_id="INC1001"),
    ]
    repo.metrics = [
        Metric(server_name="server-01", metric_name="disk_used_pct", metric_value=95.0, mount_point="/",
               metric_timestamp=NOW - timedelta(minutes=30)),
        Metric(server_name="server-01", metric_name="tmp_dir_gb", metric_value=18.4, mount_point="/tmp",
               metric_timestamp=NOW - timedelta(minutes=30)),
    ]


@pytest.fixture
def repo() -> InMemoryRepository:
    repository = InMemoryRepository()
    _seed(repository)
    return repository


@pytest.fixture
def audit(repo) -> AuditService:
    return AuditService(repo)


@pytest.fixture
def incident_service(repo, audit) -> IncidentService:
    return IncidentService(repo, audit)


@pytest.fixture
def valid_payload() -> dict:
    return {
        "incident_id": "inc2001",          # lowercase on purpose: should be normalized
        "source": "ServiceNow",
        "server_name": " Server-02 ",
        "environment": "prod",
        "severity": "P1",
        "category": "cpu",
        "description": "CPU utilization above 90% for 20 minutes.",
    }
