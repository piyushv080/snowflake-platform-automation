"""Stage 3 tests: incident parsing, validation, storage, lifecycle, audit."""
import pytest

from core.exceptions import IncidentValidationError, InvalidStateTransitionError, NotFoundError
from core.models import (
    AuditAction,
    Category,
    Environment,
    IncidentFilter,
    IncidentStatus,
    Severity,
    Source,
)


# --- valid incident parsing -------------------------------------------------
def test_valid_incident_is_normalized_and_stored(incident_service, valid_payload):
    incident = incident_service.ingest(valid_payload)

    assert incident.incident_id == "INC2001"
    assert incident.source == Source.SERVICENOW
    assert incident.server_name == "server-02"
    assert incident.environment == Environment.PRODUCTION
    assert incident.severity == Severity.CRITICAL
    assert incident.category == Category.HIGH_CPU
    assert incident.status == IncidentStatus.OPEN
    assert incident.created_at is not None


def test_extra_fields_are_ignored(incident_service, valid_payload):
    valid_payload["unexpected_field"] = "ignored"
    assert incident_service.ingest(valid_payload).incident_id == "INC2001"


# --- invalid incident data --------------------------------------------------
@pytest.mark.parametrize(
    "field, bad_value, expected_fragment",
    [
        ("incident_id", "TICKET-1", "incident_id"),
        ("server_name", "server-01; rm -rf /", "server_name"),     # shell injection attempt
        ("severity", "apocalyptic", "severity"),
        ("environment", "mars", "environment"),
        ("category", "coffee_machine", "category"),
        ("description", "short", "description"),
    ],
)
def test_invalid_values_are_rejected(incident_service, valid_payload, field, bad_value, expected_fragment):
    valid_payload[field] = bad_value
    with pytest.raises(IncidentValidationError) as error:
        incident_service.ingest(valid_payload)
    assert expected_fragment in str(error.value)


def test_missing_required_field_is_rejected(incident_service, valid_payload):
    del valid_payload["server_name"]
    with pytest.raises(IncidentValidationError, match="server_name"):
        incident_service.ingest(valid_payload)


def test_duplicate_incident_is_rejected(incident_service, valid_payload):
    incident_service.ingest(valid_payload)
    with pytest.raises(IncidentValidationError, match="already exists"):
        incident_service.ingest(valid_payload)


# --- audit record creation --------------------------------------------------
def test_ingest_creates_audit_record(incident_service, repo, valid_payload):
    incident_service.ingest(valid_payload, actor="tester")
    events = repo.list_audit_events("INC2001")
    assert [e.action for e in events] == [AuditAction.INCIDENT_CREATED]
    assert events[0].actor == "tester"
    assert events[0].details["severity"] == "critical"


def test_invalid_payload_is_audited_without_incident_id(incident_service, repo, valid_payload):
    valid_payload["severity"] = "unknown"
    with pytest.raises(IncidentValidationError):
        incident_service.ingest(valid_payload)
    rejected = [e for e in repo.audit_events if e.action == AuditAction.INCIDENT_REJECTED_INVALID]
    assert len(rejected) == 1
    assert rejected[0].incident_id is None


# --- lifecycle --------------------------------------------------------------
def test_allowed_status_transition_is_applied_and_audited(incident_service, repo):
    updated = incident_service.change_status("INC1001", IncidentStatus.DIAGNOSED, actor="tester")
    assert updated.status == IncidentStatus.DIAGNOSED
    change = repo.list_audit_events("INC1001")[-1]
    assert change.action == AuditAction.STATUS_CHANGED
    assert change.details == {"from_status": "OPEN", "to_status": "DIAGNOSED", "reason": ""}


def test_skipping_lifecycle_steps_is_refused(incident_service):
    # OPEN -> RESOLVED would bypass diagnosis, approval, and verification.
    with pytest.raises(InvalidStateTransitionError):
        incident_service.change_status("INC1001", IncidentStatus.RESOLVED, actor="tester")


def test_resolved_is_terminal(incident_service):
    with pytest.raises(InvalidStateTransitionError):
        incident_service.change_status("INC0901", IncidentStatus.OPEN, actor="tester")


def test_unknown_incident_raises_not_found(incident_service):
    with pytest.raises(NotFoundError):
        incident_service.get("INC9999")


# --- filtering --------------------------------------------------------------
def test_filter_by_status_and_category(incident_service):
    open_disk = incident_service.list(IncidentFilter(status="OPEN", category="disk_space"))
    assert [i.incident_id for i in open_disk] == ["INC1001"]
