"""Audit service: one place that writes audit events.

Every service calls `audit.record(...)` for every meaningful action.
Keeping it in one class makes it easy to guarantee "every remediation
attempt is audited" and to test it.
"""
from __future__ import annotations

from typing import Any, Optional

from core.models import AuditAction, AuditEvent
from database.repositories import AuditRepository


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    def record(
        self,
        action: AuditAction,
        actor: str,
        incident_id: Optional[str] = None,
        **details: Any,
    ) -> AuditEvent:
        event = AuditEvent(incident_id=incident_id, action=action, actor=actor, details=details)
        self.repository.add_audit_event(event)
        return event

    def timeline(self, incident_id: str) -> list[AuditEvent]:
        return self.repository.list_audit_events(incident_id=incident_id)
