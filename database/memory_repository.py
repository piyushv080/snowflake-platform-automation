"""In-memory repository: a pure-Python stand-in for Snowflake.

Used by the automated tests (fast, no network, no cost) and useful as a
reference when writing the SQLite/PostgreSQL version later.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.models import (
    AuditEvent,
    Diagnosis,
    Incident,
    IncidentFilter,
    IncidentStatus,
    LogEntry,
    Metric,
    Playbook,
    RemediationExecution,
    Runbook,
    Server,
)
from database.repositories import OpsRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryRepository(OpsRepository):
    def __init__(self) -> None:
        self.incidents: dict[str, Incident] = {}
        self.logs: list[LogEntry] = []
        self.metrics: list[Metric] = []
        self.servers: dict[str, Server] = {}
        self.playbooks: list[Playbook] = []
        self.runbooks: list[Runbook] = []
        self.audit_events: list[AuditEvent] = []
        self.diagnoses: list[Diagnosis] = []
        self.executions: dict[str, RemediationExecution] = {}

    # --- incidents ------------------------------------------------------
    def insert_incident(self, incident: Incident) -> None:
        stored = incident.model_copy(update={"created_at": _now(), "updated_at": _now()})
        self.incidents[incident.incident_id] = stored

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        incident = self.incidents.get(incident_id)
        return copy.deepcopy(incident) if incident else None

    def list_incidents(self, filters: Optional[IncidentFilter] = None) -> list[Incident]:
        filters = filters or IncidentFilter()
        result = []
        for incident in self.incidents.values():
            if filters.severity and incident.severity != filters.severity:
                continue
            if filters.environment and incident.environment != filters.environment:
                continue
            if filters.category and incident.category != filters.category:
                continue
            if filters.status and incident.status != filters.status:
                continue
            result.append(copy.deepcopy(incident))
        return sorted(result, key=lambda i: i.created_at or _now(), reverse=True)

    def update_incident_status(self, incident_id: str, status: IncidentStatus) -> None:
        incident = self.incidents[incident_id]
        self.incidents[incident_id] = incident.model_copy(update={"status": status, "updated_at": _now()})

    # --- observability ----------------------------------------------------
    def get_logs(self, server_name: str, limit: int = 50) -> list[LogEntry]:
        rows = [log for log in self.logs if log.server_name == server_name]
        return sorted(rows, key=lambda r: r.log_timestamp, reverse=True)[:limit]

    def get_metrics(self, server_name: str) -> list[Metric]:
        return [m for m in self.metrics if m.server_name == server_name]

    def get_historical_incidents(self, category: str, limit: int = 5) -> list[Incident]:
        rows = [
            i for i in self.incidents.values()
            if i.category.value == category and i.status == IncidentStatus.RESOLVED
        ]
        return rows[:limit]

    def add_metric(self, metric: Metric) -> None:
        self.metrics.append(metric)

    # --- inventory ----------------------------------------------------------
    def get_server(self, server_name: str) -> Optional[Server]:
        return self.servers.get(server_name)

    def list_playbooks(self) -> list[Playbook]:
        return list(self.playbooks)

    # --- knowledge ----------------------------------------------------------
    def list_runbooks(self, approved_only: bool = True) -> list[Runbook]:
        return [r for r in self.runbooks if r.approved or not approved_only]

    # --- diagnosis ----------------------------------------------------------
    def save_diagnosis(self, diagnosis: Diagnosis) -> str:
        diagnosis_id = str(uuid.uuid4())
        self.diagnoses.append(diagnosis.model_copy(update={"diagnosis_id": diagnosis_id, "created_at": _now()}))
        return diagnosis_id

    def get_latest_diagnosis(self, incident_id: str) -> Optional[Diagnosis]:
        rows = [d for d in self.diagnoses if d.incident_id == incident_id]
        return rows[-1] if rows else None

    # --- executions ---------------------------------------------------------
    def create_execution(self, execution: RemediationExecution) -> str:
        execution_id = str(uuid.uuid4())
        self.executions[execution_id] = execution.model_copy(
            update={"execution_id": execution_id, "created_at": _now()})
        return execution_id

    def get_execution(self, execution_id: str) -> Optional[RemediationExecution]:
        execution = self.executions.get(execution_id)
        return copy.deepcopy(execution) if execution else None

    def update_execution(self, execution: RemediationExecution) -> None:
        if execution.execution_id not in self.executions:
            raise KeyError(execution.execution_id)
        self.executions[execution.execution_id] = copy.deepcopy(execution)

    def list_executions(self, incident_id: Optional[str] = None, limit: int = 100) -> list[RemediationExecution]:
        rows = [e for e in self.executions.values() if incident_id is None or e.incident_id == incident_id]
        return sorted(rows, key=lambda e: e.created_at, reverse=True)[:limit]

    # --- audit --------------------------------------------------------------
    def add_audit_event(self, event: AuditEvent) -> None:
        self.audit_events.append(
            event.model_copy(update={"audit_id": str(uuid.uuid4()), "created_at": _now()})
        )

    def list_audit_events(self, incident_id: Optional[str] = None, limit: int = 200) -> list[AuditEvent]:
        rows = [e for e in self.audit_events if incident_id is None or e.incident_id == incident_id]
        return rows[-limit:]
