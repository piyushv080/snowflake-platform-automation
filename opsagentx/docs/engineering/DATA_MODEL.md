# Data Model and Data Flow

Database `OPSAGENTX_DB`, schema `OPSAGENTX_SCHEMA`. Defined in `sql/01_setup.sql`.
No views exist. Snowflake **enforces `NOT NULL` only**; `PRIMARY KEY`/`REFERENCES` are declared
for documentation and enforced in Python services.

## 1. ER diagram (actual tables)

```mermaid
erDiagram
    SERVERS ||--o{ INCIDENTS : "SERVER_NAME (logical)"
    SERVERS ||--o{ INFRASTRUCTURE_LOGS : "SERVER_NAME (logical)"
    SERVERS ||--o{ SERVER_METRICS : "SERVER_NAME (logical)"
    INCIDENTS ||--o{ INFRASTRUCTURE_LOGS : "INCIDENT_ID (nullable)"
    INCIDENTS ||--o{ INCIDENT_DIAGNOSIS : "INCIDENT_ID (declared FK)"
    INCIDENTS ||--o{ REMEDIATION_EXECUTIONS : "INCIDENT_ID (declared FK)"
    INCIDENT_DIAGNOSIS ||--o{ REMEDIATION_EXECUTIONS : "DIAGNOSIS_ID (logical)"
    PLAYBOOKS ||--o{ REMEDIATION_EXECUTIONS : "PLAYBOOK_NAME (logical)"
    INCIDENTS ||--o{ AUDIT_LOGS : "INCIDENT_ID (nullable)"
    RUNBOOKS }o--o{ INCIDENT_DIAGNOSIS : "RUNBOOKS_USED array"

    SERVERS { varchar SERVER_NAME PK  varchar ENVIRONMENT  varchar OS  varchar ROLE_DESCRIPTION  boolean ALLOW_AUTOMATION }
    PLAYBOOKS { varchar PLAYBOOK_NAME PK  varchar CATEGORY  varchar DESCRIPTION  array ALLOWED_ENVIRONMENTS  varchar RISK_LEVEL  boolean ENABLED }
    INCIDENTS { varchar INCIDENT_ID PK  varchar SOURCE  varchar SERVER_NAME  varchar ENVIRONMENT  varchar SEVERITY  varchar CATEGORY  varchar DESCRIPTION  varchar STATUS  timestamp CREATED_AT  timestamp UPDATED_AT }
    INCIDENT_DIAGNOSIS { varchar DIAGNOSIS_ID PK  varchar INCIDENT_ID FK  varchar ROOT_CAUSE  number CONFIDENCE  varchar RECOMMENDED_ACTION  varchar RECOMMENDED_PLAYBOOK  boolean REQUIRES_HUMAN_APPROVAL  boolean POLICY_VALID  array POLICY_MESSAGES  array TOOLS_USED  array RUNBOOKS_USED  varchar AI_MODEL  varchar ORCHESTRATION_MODE  varchar RAW_RESPONSE  timestamp CREATED_AT }
    INFRASTRUCTURE_LOGS { number LOG_ID PK  varchar SERVER_NAME  varchar LOG_LEVEL  varchar LOG_MESSAGE  timestamp LOG_TIMESTAMP  varchar INCIDENT_ID }
    SERVER_METRICS { number METRIC_ID PK  varchar SERVER_NAME  varchar METRIC_NAME  float METRIC_VALUE  varchar MOUNT_POINT  timestamp METRIC_TIMESTAMP }
    RUNBOOKS { varchar RUNBOOK_ID PK  varchar TITLE  varchar CATEGORY  varchar DESCRIPTION  varchar CONTENT  boolean APPROVED }
    REMEDIATION_EXECUTIONS { varchar EXECUTION_ID PK  varchar INCIDENT_ID FK  varchar DIAGNOSIS_ID  varchar PLAYBOOK_NAME  varchar TARGET_SERVER  varchar APPROVAL_STATUS  varchar APPROVED_BY  varchar APPROVAL_COMMENT  timestamp APPROVED_AT  varchar EXECUTION_STATUS  varchar EXECUTION_OUTPUT  variant VERIFICATION_RESULT  timestamp CREATED_AT  timestamp COMPLETED_AT }
    AUDIT_LOGS { varchar AUDIT_ID PK  varchar INCIDENT_ID  varchar ACTION  varchar ACTOR  variant DETAILS  timestamp CREATED_AT }
```

## 2. Table reference

| Table | Purpose | Notable columns | Written by | Read by |
|---|---|---|---|---|
| `INCIDENTS` | The unit of work | `STATUS` (8 states), `ENVIRONMENT`, `CATEGORY` | `IncidentService`, `RESET_DEMO_DATA` | everything |
| `INCIDENT_DIAGNOSIS` | Validated AI results (history kept) | `POLICY_VALID`, `POLICY_MESSAGES`, `TOOLS_USED`, `RUNBOOKS_USED`, `ORCHESTRATION_MODE` (`direct_llm`/`cortex_agent`), `RAW_RESPONSE` (≤16k chars) | `DiagnosisService._store` | UI, `ApprovalService` (latest) |
| `INFRASTRUCTURE_LOGS` | Log lines | `INCIDENT_ID` nullable | `RESET_DEMO_DATA` | tools |
| `SERVER_METRICS` | Time series: `disk_used_pct`, `tmp_dir_gb`, `cpu_pct`, `memory_pct`, `service_up` | `MOUNT_POINT` (mount or service name) | `RESET_DEMO_DATA`, `RemediationService` (simulated post-state) | tools, verification, UI charts |
| `RUNBOOKS` | Knowledge base (change tracking on) | `APPROVED` (only TRUE indexed) | `RESET_DEMO_DATA` | `RUNBOOK_SEARCH`, keyword fallback |
| `SERVERS` | Target allowlist | `ALLOW_AUTOMATION`, `ENVIRONMENT` | `RESET_DEMO_DATA` | `PolicyEngine`, tools |
| `PLAYBOOKS` | Playbook allowlist | `ALLOWED_ENVIRONMENTS` ARRAY, `RISK_LEVEL`, `ENABLED` | `RESET_DEMO_DATA` | `PolicyEngine`, UI |
| `REMEDIATION_EXECUTIONS` | One row per attempt (approval + run + verification) | `APPROVAL_STATUS` (PENDING/APPROVED/REJECTED), `EXECUTION_STATUS` (7 values), `VERIFICATION_RESULT` VARIANT | `ApprovalService`, `RemediationService` | UI, gate |
| `AUDIT_LOGS` | Append-only trail | `ACTION` (20 values), `ACTOR`, `DETAILS` VARIANT | `AuditService` | Audit page |

## 3. States

* Incident: `OPEN → DIAGNOSED → PENDING_APPROVAL → APPROVED → REMEDIATING → RESOLVED | REMEDIATION_FAILED`,
  plus `REJECTED`; see `core/models.ALLOWED_TRANSITIONS`.
* Execution: `NOT_STARTED → RUNNING → SUCCEEDED | FAILED | TIMED_OUT | VERIFICATION_FAILED`;
  rejected requests go straight to `CANCELLED`.

Relationships: an incident has many diagnoses (latest wins), many executions (one per attempt);
each execution references the diagnosis it came from and freezes playbook + target at creation.

## 4. Seed data (from `RESET_DEMO_DATA`)

| Incident | Server (env) | Category / severity | Purpose |
|---|---|---|---|
| INC1001 | server-01 (development) | disk_space / high | main demo |
| INC1002 | server-02 (test) | high_cpu / high | CPU demo |
| INC1003 | server-03 (development) | service_failure / critical | stopped nginx |
| INC1004 | prod-db-01 (production, automation disallowed) | disk_space / critical | policy block |
| INC0901-INC0904 | various | RESOLVED | history for `get_historical_incidents` |

## 5. Data movement for INC1001 (from the live Stage 7 run)

| Step | Component | Data read | Data written |
|---|---|---|---|
| 1 | `RESET_DEMO_DATA` | - | INCIDENTS row `OPEN`; 4 logs; 7 metrics; audit `INCIDENT_CREATED` |
| 2 | `DiagnosisService` (agent) | - | audit `DIAGNOSIS_REQUESTED` |
| 3 | Agent tools | INCIDENTS, SERVERS, LOGS, METRICS, RUNBOOK_SEARCH, PLAYBOOKS | - |
| 4 | `DiagnosisService._store` | PLAYBOOKS, SERVERS (policy) | INCIDENT_DIAGNOSIS (`disk_cleanup.yml`, 0.95, valid, 7 tools); audit `TOOL_CALLED`×7, `RUNBOOKS_RETRIEVED`, `DIAGNOSIS_COMPLETED`, `POLICY_VALIDATED`; status `DIAGNOSED` |
| 5 | `ApprovalService.request_approval` | latest diagnosis | REMEDIATION_EXECUTIONS `PENDING/NOT_STARTED`; status `PENDING_APPROVAL`; audit |
| 6 | `ApprovalService.approve` | SERVERS, PLAYBOOKS | `APPROVED`, `APPROVED_BY=PIYUSHVERMA701`, comment; status `APPROVED`; audit |
| 7 | `RemediationService.execute` | execution, incident, policy | `RUNNING`; status `REMEDIATING`; metrics `disk_used_pct=71.5`, `tmp_dir_gb=1.2`; `SUCCEEDED`, `VERIFICATION_RESULT {before 95, after 71.5, passed}`; status `RESOLVED`; audit ×6 incl. `TICKET_UPDATED` |

Total: 24 audit rows for INC1001 in that run.

## 6. Ingestion, transformation, retrieval

* **Ingestion**: `IncidentService.ingest` (UI form) and `RESET_DEMO_DATA`. No streaming/Snowpipe/API
  ingestion (**NOT IMPLEMENTED**).
* **Transformation**: Pydantic normalization (aliases, case, trimming); `TOOL_*` UDF aggregations
  (`ARRAY_AGG`, `MAX_BY`/`MIN_BY`); Cortex Search indexing of `TITLE || DESCRIPTION || CONTENT`.
* **Retrieval**: parameterized `SELECT`s in `SnowflakeRepository`; UDF calls by the agent; Cortex Search.

## 7. Retention considerations

Not configured (**NOT IMPLEMENTED**): no retention policy, no `DATA_RETENTION_TIME_IN_DAYS`
override (account default applies), no archival. `RESET_DEMO_DATA` truncates **all** tables
including `AUDIT_LOGS` - acceptable for a demo, not for production. The Cortex Search target lag
(1 hour) must stay below the table's Time Travel retention.
