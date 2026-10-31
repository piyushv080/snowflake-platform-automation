"""Repository interfaces (the "ports" between business logic and storage).

Business services depend ONLY on these abstract classes. Two
implementations exist:

* SnowflakeRepository  (database/snowflake_repository.py) - used in Snowflake
* InMemoryRepository   (database/memory_repository.py)    - used by tests

The future local version adds e.g. a SQLiteRepository / PostgresRepository
that implements the same methods. No service code has to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
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


class IncidentRepository(ABC):
    @abstractmethod
    def insert_incident(self, incident: Incident) -> None: ...

    @abstractmethod
    def get_incident(self, incident_id: str) -> Optional[Incident]: ...

    @abstractmethod
    def list_incidents(self, filters: Optional[IncidentFilter] = None) -> list[Incident]: ...

    @abstractmethod
    def update_incident_status(self, incident_id: str, status: IncidentStatus) -> None: ...


class ObservabilityRepository(ABC):
    """Read-only access to logs, metrics, and incident history."""

    @abstractmethod
    def get_logs(self, server_name: str, limit: int = 50) -> list[LogEntry]: ...

    @abstractmethod
    def get_metrics(self, server_name: str) -> list[Metric]: ...

    @abstractmethod
    def get_historical_incidents(self, category: str, limit: int = 5) -> list[Incident]: ...

    @abstractmethod
    def add_metric(self, metric: Metric) -> None:
        """Record a metric sample (used by the simulator to report post-remediation state)."""


class InventoryRepository(ABC):
    """Allowlists used by the security policy engine."""

    @abstractmethod
    def get_server(self, server_name: str) -> Optional[Server]: ...

    @abstractmethod
    def list_playbooks(self) -> list[Playbook]: ...


class KnowledgeRepository(ABC):
    """Runbook storage. Semantic search is added in Stage 5."""

    @abstractmethod
    def list_runbooks(self, approved_only: bool = True) -> list[Runbook]: ...


class DiagnosisRepository(ABC):
    @abstractmethod
    def save_diagnosis(self, diagnosis: Diagnosis) -> str:
        """Store a diagnosis and return its DIAGNOSIS_ID."""

    @abstractmethod
    def get_latest_diagnosis(self, incident_id: str) -> Optional[Diagnosis]: ...


class ExecutionRepository(ABC):
    @abstractmethod
    def create_execution(self, execution: RemediationExecution) -> str:
        """Store a new remediation execution and return its EXECUTION_ID."""

    @abstractmethod
    def get_execution(self, execution_id: str) -> Optional[RemediationExecution]: ...

    @abstractmethod
    def update_execution(self, execution: RemediationExecution) -> None:
        """Persist approval/execution fields of an existing execution."""

    @abstractmethod
    def list_executions(self, incident_id: Optional[str] = None, limit: int = 100) -> list[RemediationExecution]: ...


class AuditRepository(ABC):
    """Append-only audit trail. There is intentionally no update/delete."""

    @abstractmethod
    def add_audit_event(self, event: AuditEvent) -> None: ...

    @abstractmethod
    def list_audit_events(self, incident_id: Optional[str] = None, limit: int = 200) -> list[AuditEvent]: ...


class OpsRepository(
    IncidentRepository,
    ObservabilityRepository,
    InventoryRepository,
    KnowledgeRepository,
    DiagnosisRepository,
    ExecutionRepository,
    AuditRepository,
    ABC,
):
    """Convenience type: one object that implements every repository role."""
