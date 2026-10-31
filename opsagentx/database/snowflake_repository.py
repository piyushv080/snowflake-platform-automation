"""Snowflake implementation of the repository interfaces (Snowpark).

All SQL that touches OpsAgentX tables lives in this one file. Every value is
passed as a bind parameter (`?`), never concatenated into SQL text, which
prevents SQL injection from incident descriptions or LLM output.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from snowflake.snowpark import Session

from core.config import settings
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


def _lower_keys(row) -> dict[str, Any]:
    """Snowflake returns UPPERCASE column names; our models use lowercase."""
    return {key.lower(): value for key, value in row.as_dict().items()}


def _parse_json(value: Any) -> Any:
    """ARRAY / VARIANT columns come back from Snowpark as JSON strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class SnowflakeRepository(OpsRepository):
    def __init__(self, session: Session) -> None:
        self.session = session
        self.t = settings.fq  # t("INCIDENTS") -> OPSAGENTX_DB.OPSAGENTX_SCHEMA.INCIDENTS

    def _query(self, sql: str, params: Optional[list] = None) -> list[dict[str, Any]]:
        rows = self.session.sql(sql, params=params or []).collect()
        return [_lower_keys(row) for row in rows]

    def _execute(self, sql: str, params: Optional[list] = None) -> None:
        self.session.sql(sql, params=params or []).collect()

    # --- incidents ------------------------------------------------------
    def insert_incident(self, incident: Incident) -> None:
        self._execute(
            f"""INSERT INTO {self.t('INCIDENTS')}
                (INCIDENT_ID, SOURCE, SERVER_NAME, ENVIRONMENT, SEVERITY, CATEGORY, DESCRIPTION, STATUS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                incident.incident_id, incident.source.value, incident.server_name,
                incident.environment.value, incident.severity.value, incident.category.value,
                incident.description, incident.status.value,
            ],
        )

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        rows = self._query(f"SELECT * FROM {self.t('INCIDENTS')} WHERE INCIDENT_ID = ?", [incident_id])
        return Incident(**rows[0]) if rows else None

    def list_incidents(self, filters: Optional[IncidentFilter] = None) -> list[Incident]:
        filters = filters or IncidentFilter()
        # Column names come from this fixed mapping, never from user input.
        clauses, params = [], []
        for column, value in (
            ("SEVERITY", filters.severity),
            ("ENVIRONMENT", filters.environment),
            ("CATEGORY", filters.category),
            ("STATUS", filters.status),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(f"SELECT * FROM {self.t('INCIDENTS')} {where} ORDER BY CREATED_AT DESC", params)
        return [Incident(**row) for row in rows]

    def update_incident_status(self, incident_id: str, status: IncidentStatus) -> None:
        self._execute(
            f"UPDATE {self.t('INCIDENTS')} SET STATUS = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE INCIDENT_ID = ?",
            [status.value, incident_id],
        )

    # --- observability ----------------------------------------------------
    def get_logs(self, server_name: str, limit: int = 50) -> list[LogEntry]:
        rows = self._query(
            f"""SELECT SERVER_NAME, LOG_LEVEL, LOG_MESSAGE, LOG_TIMESTAMP, INCIDENT_ID
                FROM {self.t('INFRASTRUCTURE_LOGS')} WHERE SERVER_NAME = ?
                ORDER BY LOG_TIMESTAMP DESC LIMIT ?""",
            [server_name, int(limit)],
        )
        return [LogEntry(**row) for row in rows]

    def get_metrics(self, server_name: str) -> list[Metric]:
        rows = self._query(
            f"""SELECT SERVER_NAME, METRIC_NAME, METRIC_VALUE, MOUNT_POINT, METRIC_TIMESTAMP
                FROM {self.t('SERVER_METRICS')} WHERE SERVER_NAME = ? ORDER BY METRIC_TIMESTAMP""",
            [server_name],
        )
        return [Metric(**row) for row in rows]

    def get_historical_incidents(self, category: str, limit: int = 5) -> list[Incident]:
        rows = self._query(
            f"""SELECT * FROM {self.t('INCIDENTS')} WHERE CATEGORY = ? AND STATUS = 'RESOLVED'
                ORDER BY CREATED_AT DESC LIMIT ?""",
            [category, int(limit)],
        )
        return [Incident(**row) for row in rows]

    def add_metric(self, metric: Metric) -> None:
        self._execute(
            f"""INSERT INTO {self.t('SERVER_METRICS')}
                (SERVER_NAME, METRIC_NAME, METRIC_VALUE, MOUNT_POINT, METRIC_TIMESTAMP)
                VALUES (?, ?, ?, ?, ?)""",
            [metric.server_name, metric.metric_name, metric.metric_value, metric.mount_point,
             metric.metric_timestamp],
        )

    # --- inventory ----------------------------------------------------------
    def get_server(self, server_name: str) -> Optional[Server]:
        rows = self._query(f"SELECT * FROM {self.t('SERVERS')} WHERE SERVER_NAME = ?", [server_name])
        return Server(**rows[0]) if rows else None

    def list_playbooks(self) -> list[Playbook]:
        rows = self._query(f"SELECT * FROM {self.t('PLAYBOOKS')}")
        for row in rows:
            row["allowed_environments"] = _parse_json(row["allowed_environments"])
        return [Playbook(**row) for row in rows]

    # --- knowledge ----------------------------------------------------------
    def list_runbooks(self, approved_only: bool = True) -> list[Runbook]:
        where = "WHERE APPROVED = TRUE" if approved_only else ""
        rows = self._query(f"SELECT * FROM {self.t('RUNBOOKS')} {where} ORDER BY RUNBOOK_ID")
        return [Runbook(**row) for row in rows]

    # --- diagnosis ----------------------------------------------------------
    def save_diagnosis(self, diagnosis: Diagnosis) -> str:
        # Generate the id client-side so we can return it without a second query.
        diagnosis_id = self._query("SELECT UUID_STRING() AS ID")[0]["id"]
        self._execute(
            f"""INSERT INTO {self.t('INCIDENT_DIAGNOSIS')}
                (DIAGNOSIS_ID, INCIDENT_ID, ROOT_CAUSE, CONFIDENCE, RECOMMENDED_ACTION, RECOMMENDED_PLAYBOOK,
                 REQUIRES_HUMAN_APPROVAL, POLICY_VALID, POLICY_MESSAGES, TOOLS_USED, RUNBOOKS_USED,
                 AI_MODEL, ORCHESTRATION_MODE, RAW_RESPONSE)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, PARSE_JSON(?), PARSE_JSON(?), PARSE_JSON(?), ?, ?, ?""",
            [
                diagnosis_id, diagnosis.incident_id, diagnosis.root_cause, diagnosis.confidence,
                diagnosis.recommended_action, diagnosis.recommended_playbook,
                diagnosis.requires_human_approval, diagnosis.policy_valid,
                json.dumps(diagnosis.policy_messages), json.dumps(diagnosis.tools_used),
                json.dumps(diagnosis.runbooks_used), diagnosis.ai_model, diagnosis.orchestration_mode,
                diagnosis.raw_response,
            ],
        )
        return diagnosis_id

    def get_latest_diagnosis(self, incident_id: str) -> Optional[Diagnosis]:
        rows = self._query(
            f"""SELECT * FROM {self.t('INCIDENT_DIAGNOSIS')} WHERE INCIDENT_ID = ?
                ORDER BY CREATED_AT DESC LIMIT 1""",
            [incident_id],
        )
        if not rows:
            return None
        row = rows[0]
        for column in ("policy_messages", "tools_used", "runbooks_used"):
            row[column] = _parse_json(row[column]) or []
        row["confidence"] = float(row["confidence"] or 0)
        row["raw_response"] = row["raw_response"] or ""
        return Diagnosis(**row)

    # --- executions ---------------------------------------------------------
    def create_execution(self, execution: RemediationExecution) -> str:
        execution_id = self._query("SELECT UUID_STRING() AS ID")[0]["id"]
        self._execute(
            f"""INSERT INTO {self.t('REMEDIATION_EXECUTIONS')}
                (EXECUTION_ID, INCIDENT_ID, DIAGNOSIS_ID, PLAYBOOK_NAME, TARGET_SERVER,
                 APPROVAL_STATUS, EXECUTION_STATUS)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [execution_id, execution.incident_id, execution.diagnosis_id, execution.playbook_name,
             execution.target_server, execution.approval_status.value, execution.execution_status.value],
        )
        return execution_id

    def get_execution(self, execution_id: str) -> Optional[RemediationExecution]:
        rows = self._query(
            f"SELECT * FROM {self.t('REMEDIATION_EXECUTIONS')} WHERE EXECUTION_ID = ?", [execution_id])
        return self._to_execution(rows[0]) if rows else None

    def update_execution(self, execution: RemediationExecution) -> None:
        # Only approval/execution fields change; identity fields (incident, playbook,
        # target) are fixed at creation so they cannot be swapped after approval.
        self._execute(
            f"""UPDATE {self.t('REMEDIATION_EXECUTIONS')} SET
                    APPROVAL_STATUS = ?, APPROVED_BY = ?, APPROVAL_COMMENT = ?, APPROVED_AT = ?,
                    EXECUTION_STATUS = ?, EXECUTION_OUTPUT = ?,
                    VERIFICATION_RESULT = PARSE_JSON(?), COMPLETED_AT = ?
                WHERE EXECUTION_ID = ?""",
            [execution.approval_status.value, execution.approved_by, execution.approval_comment,
             execution.approved_at, execution.execution_status.value, execution.execution_output,
             json.dumps(execution.verification_result) if execution.verification_result is not None else None,
             execution.completed_at, execution.execution_id],
        )

    def list_executions(self, incident_id: Optional[str] = None, limit: int = 100) -> list[RemediationExecution]:
        if incident_id:
            rows = self._query(
                f"""SELECT * FROM {self.t('REMEDIATION_EXECUTIONS')} WHERE INCIDENT_ID = ?
                    ORDER BY CREATED_AT DESC LIMIT ?""", [incident_id, int(limit)])
        else:
            rows = self._query(
                f"SELECT * FROM {self.t('REMEDIATION_EXECUTIONS')} ORDER BY CREATED_AT DESC LIMIT ?",
                [int(limit)])
        return [self._to_execution(row) for row in rows]

    @staticmethod
    def _to_execution(row: dict[str, Any]) -> RemediationExecution:
        row["verification_result"] = _parse_json(row.get("verification_result"))
        return RemediationExecution(**row)

    # --- audit --------------------------------------------------------------
    def add_audit_event(self, event: AuditEvent) -> None:
        # PARSE_JSON turns the JSON string into a VARIANT so DETAILS stays queryable.
        self._execute(
            f"""INSERT INTO {self.t('AUDIT_LOGS')} (INCIDENT_ID, ACTION, ACTOR, DETAILS)
                SELECT ?, ?, ?, PARSE_JSON(?)""",
            [event.incident_id, event.action.value, event.actor, json.dumps(event.details, default=str)],
        )

    def list_audit_events(self, incident_id: Optional[str] = None, limit: int = 200) -> list[AuditEvent]:
        if incident_id:
            rows = self._query(
                f"""SELECT * FROM {self.t('AUDIT_LOGS')} WHERE INCIDENT_ID = ?
                    ORDER BY CREATED_AT LIMIT ?""",
                [incident_id, int(limit)],
            )
        else:
            rows = self._query(
                f"SELECT * FROM {self.t('AUDIT_LOGS')} ORDER BY CREATED_AT DESC LIMIT ?", [int(limit)]
            )
        for row in rows:
            row["details"] = _parse_json(row["details"]) or {}
        return [AuditEvent(**row) for row in rows]
