"""Incident service: workflow steps 1-3 (receive, validate/normalize, store).

Also owns the incident lifecycle: every status change goes through
`change_status`, which enforces the allowed transitions and audits them.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError

from core.exceptions import IncidentValidationError, InvalidStateTransitionError, NotFoundError
from core.models import (
    AuditAction,
    Incident,
    IncidentCreate,
    IncidentFilter,
    IncidentStatus,
    can_transition,
)
from database.repositories import IncidentRepository
from services.audit_service import AuditService


def _format_validation_error(error: ValidationError) -> list[str]:
    """Turn Pydantic's error list into short human-readable messages."""
    messages = []
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"]) or "incident"
        messages.append(f"{field}: {item['msg']}")
    return messages


class IncidentService:
    def __init__(self, repository: IncidentRepository, audit: AuditService) -> None:
        self.repository = repository
        self.audit = audit

    # ------------------------------------------------------------------
    def ingest(self, raw_incident: dict[str, Any], actor: str = "ingestion-api") -> Incident:
        """Validate, normalize, store, and audit a new incident.

        Raises IncidentValidationError with readable reasons if the payload is bad.
        Invalid payloads are audited too, so rejected input is visible later.
        """
        try:
            validated = IncidentCreate(**raw_incident)
        except ValidationError as error:
            reasons = _format_validation_error(error)
            self.audit.record(
                AuditAction.INCIDENT_REJECTED_INVALID,
                actor,
                incident_id=None,  # we cannot trust the id of an invalid payload
                reasons=reasons,
                received_keys=sorted(raw_incident.keys()),
            )
            raise IncidentValidationError("; ".join(reasons)) from error

        if self.repository.get_incident(validated.incident_id):
            raise IncidentValidationError(f"incident_id {validated.incident_id} already exists")

        incident = Incident(**validated.model_dump(), status=IncidentStatus.OPEN)
        self.repository.insert_incident(incident)
        self.audit.record(
            AuditAction.INCIDENT_CREATED,
            actor,
            incident_id=incident.incident_id,
            source=incident.source.value,
            severity=incident.severity.value,
            server=incident.server_name,
        )
        return self.get(incident.incident_id)

    # ------------------------------------------------------------------
    def get(self, incident_id: str) -> Incident:
        incident = self.repository.get_incident(incident_id)
        if incident is None:
            raise NotFoundError(f"incident {incident_id} not found")
        return incident

    def list(self, filters: Optional[IncidentFilter] = None) -> list[Incident]:
        return self.repository.list_incidents(filters)

    # ------------------------------------------------------------------
    def change_status(self, incident_id: str, new_status: IncidentStatus, actor: str, reason: str = "") -> Incident:
        """Move an incident to a new status, enforcing the lifecycle rules."""
        incident = self.get(incident_id)
        if not can_transition(incident.status, new_status):
            raise InvalidStateTransitionError(
                f"{incident_id}: cannot move from {incident.status.value} to {new_status.value}"
            )
        self.repository.update_incident_status(incident_id, new_status)
        self.audit.record(
            AuditAction.STATUS_CHANGED,
            actor,
            incident_id=incident_id,
            from_status=incident.status.value,
            to_status=new_status.value,
            reason=reason,
        )
        return self.get(incident_id)
