"""Domain models: the vocabulary of OpsAgentX.

These Pydantic models are plain Python. They do not import Snowflake,
so the same file works unchanged in the future local (FastAPI/SQLite) version.

Pydantic gives us two things:
1. Validation  - reject bad data with a clear error message.
2. Normalization - turn "P1", "Sev1", "CRIT" into one canonical "critical".
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Category(str, Enum):
    DISK_SPACE = "disk_space"
    HIGH_CPU = "high_cpu"
    MEMORY = "memory"
    SERVICE_FAILURE = "service_failure"
    ANSIBLE_FAILURE = "ansible_failure"
    HEALTH_CHECK = "health_check"


class Source(str, Enum):
    SERVICENOW = "servicenow"
    GITHUB = "github"
    MONITORING = "monitoring"
    ANSIBLE = "ansible"
    MANUAL = "manual"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    DIAGNOSED = "DIAGNOSED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REMEDIATING = "REMEDIATING"
    RESOLVED = "RESOLVED"
    REMEDIATION_FAILED = "REMEDIATION_FAILED"


# Which status may follow which. Anything not listed is refused.
# This is the incident lifecycle as a small state machine.
ALLOWED_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.OPEN: {IncidentStatus.DIAGNOSED},
    IncidentStatus.DIAGNOSED: {IncidentStatus.PENDING_APPROVAL, IncidentStatus.DIAGNOSED},
    IncidentStatus.PENDING_APPROVAL: {IncidentStatus.APPROVED, IncidentStatus.REJECTED},
    IncidentStatus.APPROVED: {IncidentStatus.REMEDIATING},
    IncidentStatus.REJECTED: {IncidentStatus.DIAGNOSED},          # re-diagnose after rejection
    IncidentStatus.REMEDIATING: {IncidentStatus.RESOLVED, IncidentStatus.REMEDIATION_FAILED},
    IncidentStatus.REMEDIATION_FAILED: {IncidentStatus.DIAGNOSED},  # try again from scratch
    IncidentStatus.RESOLVED: set(),                                 # terminal
}


def can_transition(current: IncidentStatus, new: IncidentStatus) -> bool:
    return new in ALLOWED_TRANSITIONS.get(current, set())


# ---------------------------------------------------------------------------
# Alias tables used during normalization
# ---------------------------------------------------------------------------
_SEVERITY_ALIASES = {
    "p1": "critical", "sev1": "critical", "crit": "critical", "1": "critical",
    "p2": "high", "sev2": "high", "2": "high",
    "p3": "medium", "sev3": "medium", "med": "medium", "3": "medium",
    "p4": "low", "sev4": "low", "4": "low",
}
_ENVIRONMENT_ALIASES = {
    "dev": "development", "develop": "development",
    "tst": "test", "qa": "test", "uat": "test",
    "prod": "production", "prd": "production",
}
_CATEGORY_ALIASES = {
    "disk": "disk_space", "filesystem": "disk_space", "disk_full": "disk_space",
    "cpu": "high_cpu", "high-cpu": "high_cpu",
    "mem": "memory", "oom": "memory",
    "service": "service_failure", "service_down": "service_failure", "service_stopped": "service_failure",
    "ansible": "ansible_failure", "deployment_failure": "ansible_failure",
    "healthcheck": "health_check", "health": "health_check",
}

INCIDENT_ID_PATTERN = re.compile(r"^INC\d{4,10}$")
SERVER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,99}$")


def _normalize_token(value: Any, aliases: dict[str, str]) -> Any:
    """Lowercase, trim, map separators, and resolve known aliases."""
    if not isinstance(value, str):
        return value
    token = value.strip().lower().replace(" ", "_")
    return aliases.get(token, token)


# ---------------------------------------------------------------------------
# Incident models
# ---------------------------------------------------------------------------
class IncidentCreate(BaseModel):
    """An incident as it arrives from an external system, before storage.

    Raises pydantic.ValidationError when data is invalid; the incident
    service converts that into IncidentValidationError.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    incident_id: str
    source: Source
    server_name: str
    environment: Environment
    severity: Severity
    category: Category
    description: str = Field(min_length=10, max_length=2000)

    @field_validator("incident_id", mode="before")
    @classmethod
    def _check_incident_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().upper()
            if not INCIDENT_ID_PATTERN.match(value):
                raise ValueError("incident_id must look like INC1001 (INC followed by 4-10 digits)")
        return value

    @field_validator("server_name", mode="before")
    @classmethod
    def _check_server_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().lower()
            # Blocks shell metacharacters, spaces, paths, etc. in host names.
            if not SERVER_NAME_PATTERN.match(value):
                raise ValueError("server_name may contain only lowercase letters, digits, '.', '-'")
        return value

    @field_validator("source", mode="before")
    @classmethod
    def _norm_source(cls, value: Any) -> Any:
        return _normalize_token(value, {"snow": "servicenow", "gh": "github", "aap": "ansible"})

    @field_validator("environment", mode="before")
    @classmethod
    def _norm_environment(cls, value: Any) -> Any:
        return _normalize_token(value, _ENVIRONMENT_ALIASES)

    @field_validator("severity", mode="before")
    @classmethod
    def _norm_severity(cls, value: Any) -> Any:
        return _normalize_token(value, _SEVERITY_ALIASES)

    @field_validator("category", mode="before")
    @classmethod
    def _norm_category(cls, value: Any) -> Any:
        return _normalize_token(value, _CATEGORY_ALIASES)


class Incident(IncidentCreate):
    """A stored incident (adds lifecycle fields)."""

    status: IncidentStatus = IncidentStatus.OPEN
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IncidentFilter(BaseModel):
    """Optional filters for listing incidents. None means 'any'."""

    severity: Optional[Severity] = None
    environment: Optional[Environment] = None
    category: Optional[Category] = None
    status: Optional[IncidentStatus] = None


# ---------------------------------------------------------------------------
# Supporting read models
# ---------------------------------------------------------------------------
class LogEntry(BaseModel):
    server_name: str
    log_level: str
    log_message: str
    log_timestamp: datetime
    incident_id: Optional[str] = None


class Metric(BaseModel):
    server_name: str
    metric_name: str
    metric_value: float
    mount_point: Optional[str] = None
    metric_timestamp: datetime


class Server(BaseModel):
    server_name: str
    environment: Environment
    os: Optional[str] = None
    role_description: Optional[str] = None
    allow_automation: bool = False


class Playbook(BaseModel):
    playbook_name: str
    category: Category
    description: Optional[str] = None
    allowed_environments: list[Environment]
    risk_level: str
    enabled: bool = True


class Runbook(BaseModel):
    runbook_id: str
    title: str
    category: str
    description: Optional[str] = None
    content: str
    approved: bool = False


class Diagnosis(BaseModel):
    """A stored diagnosis: the validated AI result plus policy outcome and provenance."""

    incident_id: str
    root_cause: str
    confidence: float
    recommended_action: str
    recommended_playbook: Optional[str] = None
    requires_human_approval: bool = True
    policy_valid: bool = False
    policy_messages: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    runbooks_used: list[str] = Field(default_factory=list)
    ai_model: str = ""
    orchestration_mode: str = "direct_llm"   # direct_llm | cortex_agent
    raw_response: str = ""
    diagnosis_id: Optional[str] = None
    created_at: Optional[datetime] = None


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExecutionStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"                      # playbook ran AND verification passed
    FAILED = "FAILED"                            # playbook reported failure
    TIMED_OUT = "TIMED_OUT"                      # no result within the time limit
    VERIFICATION_FAILED = "VERIFICATION_FAILED"  # playbook "succeeded" but the problem is still there
    CANCELLED = "CANCELLED"                      # approval rejected, never executed


class RemediationExecution(BaseModel):
    """One remediation attempt: approval decision + execution outcome."""

    incident_id: str
    playbook_name: str
    target_server: str
    diagnosis_id: Optional[str] = None
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: Optional[str] = None
    approval_comment: Optional[str] = None
    approved_at: Optional[datetime] = None
    execution_status: ExecutionStatus = ExecutionStatus.NOT_STARTED
    execution_output: Optional[str] = None
    verification_result: Optional[dict[str, Any]] = None
    execution_id: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class AuditAction(str, Enum):
    INCIDENT_CREATED = "INCIDENT_CREATED"
    INCIDENT_REJECTED_INVALID = "INCIDENT_REJECTED_INVALID"
    STATUS_CHANGED = "STATUS_CHANGED"
    DIAGNOSIS_REQUESTED = "DIAGNOSIS_REQUESTED"
    TOOL_CALLED = "TOOL_CALLED"
    RUNBOOKS_RETRIEVED = "RUNBOOKS_RETRIEVED"
    DIAGNOSIS_COMPLETED = "DIAGNOSIS_COMPLETED"
    DIAGNOSIS_FAILED = "DIAGNOSIS_FAILED"
    POLICY_VALIDATED = "POLICY_VALIDATED"
    POLICY_REJECTED = "POLICY_REJECTED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_FINISHED = "EXECUTION_FINISHED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    TICKET_UPDATED = "TICKET_UPDATED"


class AuditEvent(BaseModel):
    incident_id: Optional[str]
    action: AuditAction
    actor: str = Field(min_length=1, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict)
    audit_id: Optional[str] = None
    created_at: Optional[datetime] = None
