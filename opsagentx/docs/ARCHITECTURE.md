# OpsAgentX architecture

## 1. Layers

```
                      ┌──────────────────────────────────────────────┐
  Streamlit UI        │ streamlit_app.py · app_pages/* · ui/common.py │   no business rules here
                      └───────────────────────┬──────────────────────┘
                                              │ calls
                      ┌───────────────────────▼──────────────────────┐
  Services            │ IncidentService  DiagnosisService             │
  (business logic,    │ ApprovalService  RemediationService           │   reusable Python
   plain Python)      │ AuditService     KnowledgeService             │
                      └──┬─────────────┬─────────────┬───────────┬───┘
                         │             │             │           │
            ┌────────────▼──┐ ┌────────▼──────┐ ┌────▼────────┐ ┌▼─────────────────┐
  Ports     │ Repositories  │ │ LLMProvider   │ │ AgentRunner │ │ AnsibleClient    │
  (ABCs)    │ (database/    │ │ (llm/base.py) │ │ RunbookRetr.│ │ TicketClient     │
            │ repositories) │ │               │ │             │ │ (integrations/)  │
            └──────┬────────┘ └──────┬────────┘ └────┬────────┘ └────────┬─────────┘
  Adapters  SnowflakeRepository  SnowflakeCortex   CortexAgentRunner   SimulatedAnsible
  (Snowflake InMemoryRepository  Provider          CortexSearchRetr.   SimulatedServiceNow
   specific)                     (AI_COMPLETE)     KeywordRetriever
```

`core/` (models, config, exceptions, security policy) is shared by every layer and has no
Snowflake imports. `services/factory.py` is the **composition root**: the only place that chooses
concrete adapters.

## 2. Incident lifecycle

```
OPEN ──diagnose──▶ DIAGNOSED ──request approval──▶ PENDING_APPROVAL ──approve──▶ APPROVED
  ▲                    ▲  ▲                               │                        │
  │                    │  └──────── re-diagnose ── REJECTED ◀──reject──┘          execute
  │                    │                                                           ▼
  │                    └──────────── re-diagnose ── REMEDIATION_FAILED ◀── REMEDIATING ──▶ RESOLVED
  └ ingest                                    (failed / timed out / not verified)   (verified only)
```

`core/models.py: ALLOWED_TRANSITIONS` enforces this state machine. Every transition writes a
`STATUS_CHANGED` audit row.

## 3. Diagnosis flow

**Agent mode** (`mode="agent"`): `CortexAgentRunner` calls
`SNOWFLAKE.CORTEX.DATA_AGENT_RUN('OPSAGENTX_DIAGNOSTIC_AGENT', ...)`. The Cortex Agent
(`claude-sonnet-4-5` orchestration) plans and calls its tools:

| Agent tool | Implementation | Access |
|---|---|---|
| `get_incident_details` | `TOOL_GET_INCIDENT_DETAILS(id)` SQL UDF | read INCIDENTS, SERVERS |
| `get_server_logs` | `TOOL_GET_SERVER_LOGS(server)` | read INFRASTRUCTURE_LOGS (30 rows) |
| `get_server_metrics` | `TOOL_GET_SERVER_METRICS(server)` | read SERVER_METRICS |
| `get_historical_incidents` | `TOOL_GET_HISTORICAL_INCIDENTS(category)` | read resolved INCIDENTS (5 rows) |
| `analyze_disk_usage` | `TOOL_ANALYZE_DISK_USAGE(server)` | read SERVER_METRICS |
| `validate_remediation` | `TOOL_VALIDATE_REMEDIATION(...)` | read PLAYBOOKS, SERVERS (advisory) |
| `search_runbooks` | Cortex Search `RUNBOOK_SEARCH` | approved runbooks only |

SQL UDFs cannot modify data, take fixed typed arguments, and contain no dynamic SQL. The agent
has no text-to-SQL, shell, or execution tool.

**Direct mode** (`mode="direct"`): `DiagnosisService.collect_evidence` calls the same tools from
Python (`agent/tools.py`) in a fixed order (disk analysis only for disk incidents), builds a prompt,
and calls `AI_COMPLETE` with a JSON schema whose `recommended_playbook` is an enum of allowlisted
names.

**Auto mode** tries the agent and falls back to direct, recording `DIAGNOSIS_FAILED` with
`fallback=direct_llm`.

**Both modes then run the same untrusting post-processing:**

1. `llm/schemas.parse_diagnosis` - extract JSON, validate with Pydantic (types, ranges, playbook
   *name* shape). Malformed → `LLMResponseError`, nothing stored.
2. `DiagnosisService._finalize` - the model cannot change the incident id or category used for
   policy, cannot waive approval, low confidence is flagged.
3. `PolicyEngine.validate_remediation` - deterministic rules (below).
4. Store `INCIDENT_DIAGNOSIS` (+ tools used, runbooks used, raw response) and audit.

## 4. Security model

| Rule | Enforced in | When |
|---|---|---|
| R1 playbook in `PLAYBOOKS` allowlist and enabled | `core/security.py` | diagnosis, approval request, approval, execution |
| R2 playbook category matches incident category | same | same |
| R3 playbook allowed in the environment | same | same |
| R4 target server in `SERVERS` inventory | same | same |
| R5 server has `ALLOW_AUTOMATION = TRUE` | same | same |
| R6 incident environment equals inventory environment | same | same |
| R7 human approval always required | `ApprovalService` | approve() needs a human actor + `confirmed=True` |
| R8 shell-like text in recommendations is flagged, never executed | `core/security.py` | diagnosis |
| Playbook/target fixed at request time | `ApprovalService.request_approval` | copied from diagnosis/incident, never user/LLM input |
| Rejected → `CANCELLED`, never runs | `ApprovalService.reject` + execution gate | always |
| No double execution | `RemediationService._check_gate` | execute |
| Failure is never success | `RemediationService.execute` | FAILED / TIMED_OUT / VERIFICATION_FAILED → `REMEDIATION_FAILED` |
| Resolve only after independent verification | `RemediationService.verify` | reads post-job metrics; no new metric → not verified |
| Append-only audit | `AuditRepository` has no update/delete | every action |
| No SQL injection | bind parameters everywhere; fixed column allowlist for filters | all SQL |
| Least privilege | `OPSAGENTX_ROLE` owns all objects; ACCOUNTADMIN only for role setup | deployment |

Defense in depth for playbook names: JSON-schema enum (direct mode) → Pydantic name-shape check →
policy allowlist (×3) → the Ansible client itself refuses unknown playbooks.

## 5. Key decisions

| Decision | Why | Trade-off |
|---|---|---|
| Call the agent via `DATA_AGENT_RUN` (SQL), not REST | Reuses the authenticated Snowpark session (no token handling), works in any Streamlit runtime and in stored procedures; outbound HTTPS from the dev sandbox fails TLS verification | No streaming; Snowflake recommends REST for most integrations |
| Streamlit **warehouse** runtime | `pydantic` from the Snowflake Anaconda channel; no PyPI/EAI needed | Older Streamlit (1.52.2); no container-only features |
| `AI_COMPLETE` (not `SNOWFLAKE.CORTEX.COMPLETE`) | The `SNOWFLAKE.CORTEX.*` completion names are deprecated | none |
| `SEARCH_PREVIEW` fallback for Cortex Search | Python API failed in the sandbox (TLS proxy) | `SEARCH_PREVIEW` is documented for testing (higher latency, 300 kB cap) |
| Python policy engine is authoritative; SQL `TOOL_VALIDATE_REMEDIATION` is advisory | LLM-facing tools must never be the security boundary | rules exist twice; tests cover both |
| Verification from metrics, not job output | A playbook can "succeed" without fixing anything | needs a monitoring signal per category |
| One `RESET_DEMO_DATA()` procedure | Script and UI reset share one data definition | demo-only |

## 6. Data model

`INCIDENTS`, `INCIDENT_DIAGNOSIS`, `INFRASTRUCTURE_LOGS`, `RUNBOOKS`, `REMEDIATION_EXECUTIONS`,
`AUDIT_LOGS` (as specified) plus three supporting tables:

* `SERVERS` - inventory and `ALLOW_AUTOMATION` allowlist.
* `PLAYBOOKS` - playbook allowlist with category, allowed environments, risk.
* `SERVER_METRICS` - time series used by tools and verification.

Snowflake enforces `NOT NULL` but not PK/FK/UNIQUE on standard tables; they are declared for
documentation and enforced in the service layer.

## 7. Snowflake objects

| Object | Type |
|---|---|
| `OPSAGENTX_ROLE` | role |
| `OPSAGENTX_DB.OPSAGENTX_SCHEMA` | database / schema |
| 9 tables (above) | tables |
| `RESET_DEMO_DATA()` | SQL stored procedure |
| `RUNBOOK_SEARCH` | Cortex Search service (approved runbooks, `CATEGORY` attribute) |
| `TOOL_*` (6) | SQL UDFs (agent tools) |
| `OPSAGENTX_DIAGNOSTIC_AGENT` | Cortex Agent |
| `APP_STAGE`, `OPSAGENTX_APP` | stage, Streamlit (warehouse runtime) |
