# Low-Level Design (LLD)

This document describes the code as it exists. Snippets marked **(actual)** are copied from the
repository; anything else is labelled **(illustrative)**. Line counts are from 2026-10-31.

## 1. Repository structure

```
opsagentx/
├── streamlit_app.py              (34)  Streamlit entry: page config, st.navigation, header
├── environment.yml                     Warehouse-runtime packages (Snowflake Anaconda channel)
├── requirements.txt                    Local test packages (pydantic, snowpark, pytest)
├── .streamlit/config.toml              Theme
├── app_pages/                          One file per page (run by st.navigation)
│   ├── dashboard.py  (82)  incidents.py (132)  diagnosis.py (117)
│   └── approvals.py  (77)  executions.py (93)  audit.py (80)
├── ui/common.py                  (137) Session, cached reads, current_user, formatting
├── core/
│   ├── models.py                 (326) Pydantic models, enums, lifecycle state machine
│   ├── security.py               (125) PolicyEngine (rules R1-R8)
│   ├── config.py                  (48) Settings from environment variables
│   └── exceptions.py              (38) Exception hierarchy
├── database/
│   ├── repositories.py           (125) Repository ABCs (ports)
│   ├── snowflake_repository.py   (257) Snowflake implementation (all SQL)
│   ├── memory_repository.py      (140) In-memory implementation (tests)
│   └── connection.py              (47) Snowpark session creation
├── llm/
│   ├── base.py                    (50) LLMProvider ABC, LLMResponse
│   ├── snowflake_cortex.py        (84) AI_COMPLETE adapter
│   └── schemas.py                (107) DiagnosisResult, JSON schema, defensive parser
├── agent/
│   ├── tools.py                  (126) DiagnosticTools (7 read-only tools), TOOL_NAMES
│   ├── orchestration.py          (133) AgentRunner ABC, CortexAgentRunner, parser
│   ├── instructions.py            (26) DIAGNOSIS_SYSTEM_PROMPT
│   └── opsagentx_agent.yaml            Cortex Agent specification (source of truth)
├── integrations/
│   ├── ansible.py                (166) AnsibleClient ABC + SimulatedAnsibleClient
│   └── servicenow.py              (40) TicketClient ABC + SimulatedServiceNowClient
├── services/
│   ├── incident_service.py       (101)  diagnosis_service.py (255)
│   ├── approval_service.py       (170)  remediation_service.py (216)
│   ├── audit_service.py           (31)  knowledge_service.py (67)
│   ├── cortex_search_retriever.py(133)  factory.py (62)
├── sql/  00_role_setup · 01_setup · 02_sample_data · 03_cortex_search · 04_agent_tools · 05_streamlit · 06_cortex_agent
├── scripts/  run_sql · deploy_streamlit · check_stage3..8 (live checks)
├── tests/    conftest + 7 test modules + fixtures/agent_response_inc1003.json
└── docs/     ARCHITECTURE.md · DEMO.md · PORTABILITY.md · engineering/*
```

Not present (despite appearing in the original suggested structure): `core/logging.py`,
`database/schemas.py`, `integrations/github.py`, `app/` directory (pages live in `app_pages/`
because Streamlit warehouse runtime requires the entry file at the root), `data/sample_runbooks/`
(runbooks live in `sql/02_sample_data.sql` so the table is the single source of truth).

## 2. `core/` - domain, policy, configuration

### 2.1 `core/models.py`

Enums (all `str, Enum`): `Severity` (low, medium, high, critical), `Environment`
(development, test, production), `Category` (disk_space, high_cpu, memory, service_failure,
ansible_failure, health_check), `Source` (servicenow, github, monitoring, ansible, manual),
`IncidentStatus` (8 states), `ApprovalStatus`, `ExecutionStatus`, `AuditAction` (20 actions).

**Lifecycle state machine (actual):**

```python
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
```

`can_transition(current, new) -> bool` returns `new in ALLOWED_TRANSITIONS[current]`.

**`IncidentCreate` (Pydantic)** - validation and normalization of incoming incidents.

| Field | Rule |
|---|---|
| `incident_id` | uppercased; must match `^INC\d{4,10}$` |
| `server_name` | lowercased; must match `^[a-z0-9][a-z0-9.\-]{0,99}$` (blocks spaces, `;`, `/`, shell metacharacters) |
| `source` | aliases `snow→servicenow`, `gh→github`, `aap→ansible` |
| `environment` | aliases `dev→development`, `qa/uat/tst→test`, `prod/prd→production` |
| `severity` | aliases `p1/sev1/crit/1→critical`, `p2→high`, `p3/med→medium`, `p4→low` |
| `category` | aliases `disk→disk_space`, `cpu→high_cpu`, `oom→memory`, `service→service_failure`, `ansible→ansible_failure`, `health→health_check` |
| `description` | 10-2000 characters |
| extra keys | ignored (`extra="ignore"`) |

Example (from `tests/test_incident_service.py`, actual):
input `{"incident_id": "inc2001", "source": "ServiceNow", "server_name": " Server-02 ", "environment": "prod", "severity": "P1", "category": "cpu", ...}`
→ `INC2001 / servicenow / server-02 / production / critical / high_cpu`.

Other models: `Incident` (adds `status`, timestamps), `IncidentFilter`, `LogEntry`, `Metric`,
`Server`, `Playbook`, `Runbook`, `Diagnosis`, `RemediationExecution`, `AuditEvent`.

### 2.2 `core/security.py` - `PolicyEngine`

`validate_remediation(playbook_name, server_name, environment, category=None, recommended_action="") -> PolicyDecision`

| Step | Rule | Message prefix |
|---|---|---|
| 1 | no playbook or `"none"` | `R1: no playbook recommended` |
| 2 | playbook not in `PLAYBOOKS` / disabled | `R1:` |
| 3 | playbook category ≠ incident category | `R2:` |
| 4 | environment not in `ALLOWED_ENVIRONMENTS` | `R3:` |
| 5 | server not in `SERVERS` | `R4:` |
| 6 | `ALLOW_AUTOMATION = FALSE` | `R5:` |
| 7 | incident environment ≠ inventory environment | `R6:` |
| 8 | approval required (always in this demo) | sets `requires_approval` |
| 9 | shell-like text in `recommended_action` | warning `R8:` (not a denial) |

`PolicyDecision` dataclass: `allowed, playbook_name, server_name, environment, requires_approval, messages, warnings, to_dict()`.
`contains_shell_command(text)` uses patterns such as `\brm\s+-`, `\bsudo\b`, `&&`, `` ` ``, `$(`.

Example (live, Stage 7): `validate_remediation("disk_cleanup.yml", "prod-db-01", "production", "disk_space")`
→ `allowed=False`, `messages=["R5: server 'prod-db-01' does not allow automated remediation"]`.

### 2.3 `core/config.py` (actual excerpt)

```python
llm_model: str = _env("OPSAGENTX_LLM_MODEL", "claude-sonnet-4-5")
llm_fallback_model: str = _env("OPSAGENTX_LLM_FALLBACK_MODEL", "llama3.3-70b")
agent_name: str = _env("OPSAGENTX_AGENT", "OPSAGENTX_DIAGNOSTIC_AGENT")
search_service: str = _env("OPSAGENTX_SEARCH_SERVICE", "RUNBOOK_SEARCH")
min_confidence: float = float(_env("OPSAGENTX_MIN_CONFIDENCE", "0.5"))
```

`settings.fq("INCIDENTS")` → `OPSAGENTX_DB.OPSAGENTX_SCHEMA.INCIDENTS`. No secrets are configured here.

### 2.4 `core/exceptions.py`

```
OpsAgentXError
├── IncidentValidationError      bad incoming data
├── NotFoundError                missing incident / execution
├── InvalidStateTransitionError  lifecycle or double-execution violation
├── LLMResponseError             model output unparsable / invalid / non-allowlisted tool
├── LLMProviderError             model or agent call failed
├── PolicyViolationError         policy denied
├── ApprovalRequiredError        missing / non-human / unconfirmed approval
└── AnsibleTimeoutError          (integrations/ansible.py) job timed out
```

The UI catches `OpsAgentXError` and shows `ui.common.show_error`.

## 3. `database/` - storage

### 3.1 Ports (`repositories.py`)

`IncidentRepository` (insert/get/list/update status), `ObservabilityRepository`
(`get_logs`, `get_metrics`, `get_historical_incidents`, `add_metric`), `InventoryRepository`
(`get_server`, `list_playbooks`), `KnowledgeRepository` (`list_runbooks`), `DiagnosisRepository`
(`save_diagnosis`, `get_latest_diagnosis`), `ExecutionRepository` (create/get/update/list),
`AuditRepository` (`add_audit_event`, `list_audit_events` - no update/delete by design),
and `OpsRepository` combining all.

### 3.2 `SnowflakeRepository`

* `_query(sql, params)` / `_execute(sql, params)` wrap `session.sql(sql, params=...).collect()`.
* Column names are lowercased (`_lower_keys`); `ARRAY`/`VARIANT` values arrive as JSON text and
  are decoded by `_parse_json`.
* `list_incidents` builds `WHERE` from a **fixed** column list; only values are bound.
* VARIANT/ARRAY writes use `INSERT ... SELECT ?, PARSE_JSON(?)` because `VALUES` cannot hold
  `PARSE_JSON`.
* `save_diagnosis` / `create_execution` fetch `UUID_STRING()` first, then insert with that id.
* `update_execution` changes approval/execution columns only - incident, playbook and target are
  immutable after creation.

### 3.3 `InMemoryRepository`

Dicts and lists with deep copies on read. Used by all unit tests; reference for a SQLite version.

### 3.4 `connection.get_session(role="OPSAGENTX_ROLE")`

Returns `get_active_session()` when inside Snowflake; otherwise builds a session from
`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_HOST` and the OAuth token file at `SNOWFLAKE_TOKEN_FILE_PATH`
(sandbox only). The Streamlit app uses `st.connection("snowflake")` instead (`ui/common._snowpark_session`).

## 4. `llm/` - LLM layer

### 4.1 `LLMProvider.generate_json(system_prompt, user_prompt, json_schema, temperature=0.0, max_tokens=1200) -> LLMResponse`

`LLMResponse(text, model, parsed=None, usage={})`.

### 4.2 `SnowflakeCortexProvider` (actual SQL)

```python
_AI_COMPLETE_SQL = """
SELECT AI_COMPLETE(
    model             => ?,
    prompt            => ?,
    model_parameters  => PARSE_JSON(?),
    response_format   => PARSE_JSON(?),
    show_details      => TRUE
) AS RESPONSE
"""
```

Flow: concatenate system prompt + `=== INPUT ===` + data → try `claude-sonnet-4-5`, then
`llama3.3-70b` → unwrap `structured_output[0].raw_message` → `LLMResponse(parsed=dict)`.
All models failing → `LLMProviderError("AI_COMPLETE failed for all models -> ...")`.

### 4.3 `llm/schemas.py`

* `DiagnosisResult`: `incident_id, category, severity, root_cause (5-2000), confidence (0-1),
  recommended_action (5-2000), recommended_playbook, requires_human_approval, evidence (≤10)`.
  The playbook validator accepts only `[a-z0-9_\-]+\.ya?ml` or `none`, so `rm -rf /tmp/*` and
  `../../etc/passwd.yml` are rejected.
* `diagnosis_json_schema(allowed_playbooks)` - schema with `recommended_playbook` enum =
  allowlist + `"none"`.
* `parse_diagnosis(text, parsed=None)` (actual):

```python
def parse_diagnosis(text: str, parsed: Optional[dict[str, Any]] = None) -> DiagnosisResult:
    """Validate a model response. Prefers provider-parsed JSON when available."""
    data = parsed if isinstance(parsed, dict) else _extract_json_object(text or "")
    try:
        return DiagnosisResult(**data)
    except ValidationError as error:
        reasons = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]
        raise LLMResponseError("invalid diagnosis -> " + "; ".join(reasons)) from error
```

`_extract_json_object` tries the whole text, then each ```` ``` ```` fence, then the first `{` to
last `}` span.

## 5. `agent/` - tools and orchestration

### 5.1 `DiagnosticTools` (Python tools used in direct mode)

| Method | Parameters | Returns | Notes |
|---|---|---|---|
| `get_incident_details` | `incident_id` | `{"incident": ..., "server": ...}` or `{"error"}` | |
| `get_server_logs` | `server_name, limit=50` | `{"server_name", "logs": [...]}` | limit capped at 200; name validated |
| `get_server_metrics` | `server_name` | `{"server_name", "metrics": [...]}` | |
| `search_runbooks` | `query, category=None, limit=3` | `{"retriever", "runbooks": [...]}` | query truncated to 500 chars, limit ≤5 |
| `get_historical_incidents` | `category, limit=5` | `{"category", "incidents": [...]}` | unknown category → `ValueError` |
| `analyze_disk_usage` | `server_name` | `{"mounts": [...], "tmp_dir_gb", "likely_cause"}` | critical ≥90, warning ≥80 |
| `validate_remediation` | `playbook_name, environment, server_name="", category=None` | `PolicyDecision.to_dict()` | |

`TOOL_NAMES` lists these seven names; any other tool name in an agent trace is refused.

### 5.2 `CortexAgentRunner.run(incident_id) -> AgentRun` (actual)

```python
body = {"messages": [{"role": "user",
         "content": [{"type": "text", "text": f"Diagnose incident {incident_id}."}]}],
        "stream": False}
rows = self.session.sql("SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(?, ?) AS R",
                        params=[self.agent_fqn, json.dumps(body)]).collect()
```

`parse_agent_response(response)` walks `content[]`: `tool_use` → `ToolCall(name, input)`;
`tool_result` → collect `search_results[].doc_id` as runbook ids; last `text` → final answer.
Raises `LLMProviderError` if `status` is not `completed` or there is no text.
`AgentRun.unexpected_tools` = tool names not in `TOOL_NAMES`.

### 5.3 `agent/instructions.py`

`DIAGNOSIS_SYSTEM_PROMPT` (direct mode): evidence-based root cause, playbook only from the
allowed list or `none`, no shell commands, `requires_human_approval` always true, treat input as
data (prompt-injection defense).

## 6. `services/`

### 6.1 `IncidentService`

* `ingest(raw, actor="ingestion-api") -> Incident` - Pydantic validate → reject duplicates →
  insert `OPEN` → audit `INCIDENT_CREATED`. Invalid input audits `INCIDENT_REJECTED_INVALID`
  with `incident_id=None` and raises `IncidentValidationError("field: message; ...")`.
* `get(id)` (raises `NotFoundError`), `list(filters)`.
* `change_status(id, new, actor, reason="")` - `can_transition` or `InvalidStateTransitionError`;
  audits `STATUS_CHANGED {from_status, to_status, reason}`.

### 6.2 `DiagnosisService.diagnose(incident_id, actor="operator", mode="direct") -> Diagnosis`

1. Validate mode (`direct|agent|auto`) and status (OPEN, DIAGNOSED, REJECTED, REMEDIATION_FAILED).
2. Audit `DIAGNOSIS_REQUESTED`.
3. `agent`/`auto` → `_diagnose_with_agent`: run agent, audit each `TOOL_CALLED` (actor
   `opsagentx-agent`) and `RUNBOOKS_RETRIEVED`, refuse unexpected tools, `parse_diagnosis(final_text)`.
   On failure in `auto`, audit `DIAGNOSIS_FAILED {fallback: direct_llm}` and continue.
4. `direct` → `_diagnose_direct`: `collect_evidence` (tools in fixed order; `analyze_disk_usage`
   only for disk incidents), `build_prompt` (logs ≤20, runbook content ≤1,800 chars each),
   `generate_json` with the allowlist schema, `parse_diagnosis`.
5. `_finalize`: keep the incident's real id and category, force `requires_human_approval=True`,
   flag confidence < 0.5, run `PolicyEngine`, collect messages.
6. `_store`: insert diagnosis, audit `DIAGNOSIS_COMPLETED` and `POLICY_VALIDATED|POLICY_REJECTED`,
   move to `DIAGNOSED`.

Example (live, Stage 6, agent mode, INC1001): `disk_cleanup.yml`, confidence 0.95, policy valid,
tools `get_incident_details, get_historical_incidents, get_server_metrics, analyze_disk_usage,
get_server_logs, search_runbooks, validate_remediation`, ~52 s.

### 6.3 `ApprovalService`

* `request_approval(incident_id, requested_by)` - requires `DIAGNOSED`, a diagnosis with a real
  playbook, and a passing policy check; creates a `PENDING` execution with playbook from the
  diagnosis and target from the incident; incident → `PENDING_APPROVAL`.
* `approve(execution_id, approver, comment="", confirmed=False)` - must be `PENDING`; approver not
  in `AUTOMATED_ACTORS`; `confirmed=True`; policy re-check (failure auto-closes the request as
  `CANCELLED` and the incident becomes `REJECTED`); incident → `APPROVED`.
* `reject(execution_id, approver, reason)` - reason ≥3 chars; execution `REJECTED/CANCELLED`;
  incident → `REJECTED`.

### 6.4 `RemediationService`

* `execute(execution_id, actor, ansible=None)` - `_check_gate` (actual snippet below) → RUNNING →
  incident `REMEDIATING` → `run_playbook` → write post-state metrics → status by outcome →
  `verify` → incident `RESOLVED` only when `SUCCEEDED` → ServiceNow note → audit.

```python
if execution.approval_status != ApprovalStatus.APPROVED:
    raise blocked(f"execution is {execution.approval_status.value}, not APPROVED", ApprovalRequiredError)
if execution.execution_status != ExecutionStatus.NOT_STARTED:
    raise blocked(f"execution already {execution.execution_status.value}", InvalidStateTransitionError)
incident = self.incidents.get(execution.incident_id)
if incident.status != IncidentStatus.APPROVED:
    raise blocked(f"incident is {incident.status.value}, not APPROVED", InvalidStateTransitionError)
decision = self.policy.validate_remediation(...)
if not decision.allowed:
    raise blocked("policy check failed at execution time: " + "; ".join(decision.messages),
                  PolicyViolationError)
```

* `verify(category, server_name, playbook_name, started_at=None)` - rule table
  `VERIFICATION_RULES`: disk `disk_used_pct < 80`, cpu `cpu_pct < 70`, memory `memory_pct < 80`,
  service `service_up == 1`. `before` = last sample before `started_at`; `after` = latest sample at
  or after it; no post-job sample → `passed=False, reason="... cannot verify"`.
  Example (live): `{"rule": "disk_used_pct < 80.0", "before": 95, "after": 71.5, "passed": true}`.
* Outcome mapping: `JobOutcome.FAILED → FAILED`; `AnsibleTimeoutError → TIMED_OUT`; any other
  exception → `FAILED`; ok + verified → `SUCCEEDED`; ok + not verified → `VERIFICATION_FAILED`.

### 6.5 Retrieval (`knowledge_service.py`, `cortex_search_retriever.py`)

* `KeywordRunbookRetriever.search` - token overlap ratio + 1.0 category boost, approved only.
* `CortexSearchRetriever.search` - request `{query, columns:[RUNBOOK_ID,TITLE,CATEGORY,CONTENT],
  limit, filter:{"@eq":{"CATEGORY": category}}}`; retry without filter if empty; try
  `snowflake.core` Python API once, then remember the error and use
  `SNOWFLAKE.CORTEX.SEARCH_PREVIEW(?, ?)`; drop hits whose reranker score is more than
  `RERANKER_MARGIN = 3.0` below the best.
* `FallbackRunbookRetriever` - primary failure → keyword fallback; `name` becomes
  `"keyword_fallback (primary failed)"`.

### 6.6 `AuditService.record(action, actor, incident_id=None, **details)` / `timeline(id)`.

### 6.7 `factory.build_snowflake_app(session) -> OpsAgentX`

Wires: `SnowflakeRepository`, `AuditService`, `PolicyEngine`,
`FallbackRunbookRetriever(CortexSearchRetriever, KeywordRunbookRetriever)`, `DiagnosticTools`,
`SnowflakeCortexProvider`, `CortexAgentRunner`, `IncidentService`, `DiagnosisService`,
`ApprovalService`, `RemediationService(SimulatedAnsibleClient(), tickets=SimulatedServiceNowClient())`.

## 7. `integrations/`

* `SimulatedAnsibleClient(scenario=SUCCESS, simulated_duration_seconds=0.5)` -
  `run_playbook(name, host, extra_vars, timeout_seconds=300)`. Unknown playbook → `ValueError`.
  Scenarios: `SUCCESS` (fixed post-state), `FAILURE` (`FAILED!` task, outcome FAILED),
  `TIMEOUT` (raises `AnsibleTimeoutError`), `VERIFICATION_FAILURE` (outcome ok, unfixed post-state).
  Post-state table `_EFFECTS`: `disk_cleanup.yml` → disk 71.5 / tmp 1.2 (unfixed 93.8 / 17.9);
  `restart_service.yml` → `service_up` 1 (unfixed 0); `restart_high_cpu_process.yml` → cpu 34 (95);
  diagnostics playbooks → no change.
* `SimulatedServiceNowClient.update_ticket(id, state, note)` - stores in memory; audited as
  `TICKET_UPDATED`. **No real HTTP integration exists.**

## 8. SQL objects

| File | Objects | Notes |
|---|---|---|
| `00_role_setup.sql` | `OPSAGENTX_ROLE`, grants | ACCOUNTADMIN, once |
| `01_setup.sql` | DB, schema, 9 tables | `CREATE ... IF NOT EXISTS` |
| `02_sample_data.sql` | `RESET_DEMO_DATA()` SQL procedure, `EXECUTE AS OWNER` | truncates 9 tables, inserts 5 servers, 6 playbooks, 6 runbooks, 8 incidents, 15 logs, 14 metrics, 4 audit rows |
| `03_cortex_search.sql` | `CHANGE_TRACKING` on RUNBOOKS, `RUNBOOK_SEARCH` | `ON SEARCH_TEXT ATTRIBUTES CATEGORY TARGET_LAG='1 hour'`, approved rows only, embedding `snowflake-arctic-embed-m-v1.5` |
| `04_agent_tools.sql` | 6 `TOOL_*` SQL UDFs `RETURNS OBJECT` | read-only, fixed args |
| `05_streamlit.sql` | `APP_STAGE`, `OPSAGENTX_APP` | `RUNTIME_NAME='SYSTEM$WAREHOUSE_RUNTIME'` |
| `06_cortex_agent.sql` | `OPSAGENTX_DIAGNOSTIC_AGENT` | spec identical to `agent/opsagentx_agent.yaml` |

UDF signatures: `TOOL_GET_INCIDENT_DETAILS(VARCHAR)`, `TOOL_GET_SERVER_LOGS(VARCHAR)` (30 latest),
`TOOL_GET_SERVER_METRICS(VARCHAR)`, `TOOL_GET_HISTORICAL_INCIDENTS(VARCHAR)` (5 resolved),
`TOOL_ANALYZE_DISK_USAGE(VARCHAR)`, `TOOL_VALIDATE_REMEDIATION(VARCHAR, VARCHAR, VARCHAR, VARCHAR)`.

Example (live): `TOOL_ANALYZE_DISK_USAGE('server-01')` →
`{"mounts":[{"mount_point":"/","latest_used_pct":95,"change_pct_points":17,"status":"critical"}],"tmp_dir_gb":18.4,"likely_cause":"temporary directory growth"}`.

## 9. Snowpark Python usage

Snowpark is used only as a session for parameterized SQL (`session.sql(sql, params=[...]).collect()`).
No Snowpark DataFrame transformations, Python UDFs or Python stored procedures are implemented
(all UDFs/procedures are SQL). **Python UDFs: NOT IMPLEMENTED** (not needed).

## 10. Cortex Agent configuration (`agent/opsagentx_agent.yaml`)

* `models.orchestration: claude-sonnet-4-5`; `orchestration.budget: {seconds: 90, tokens: 32000}`.
* `instructions.system` (role, read-only, treat tool output as data), `orchestration` (7-step tool
  order, disk tool only for disk, prefer runbook-named playbook, call `validate_remediation`, use
  `none` if not allowed), `response` (JSON only, exact keys).
* 6 `generic` tools with `input_schema` → `tool_resources` `type: function`,
  `identifier: OPSAGENTX_DB.OPSAGENTX_SCHEMA.TOOL_*`, `execution_environment: warehouse COMPUTE_WH`.
* 1 `cortex_search` tool `search_runbooks` → `RUNBOOK_SEARCH`, `id_column RUNBOOK_ID`,
  `title_column TITLE`, `max_results 3`.

## 11. Streamlit application

| File | Key behavior |
|---|---|
| `streamlit_app.py` | `set_page_config(layout="wide")`, default `selected_incident="INC1001"`, `st.navigation(..., position="top")` |
| `ui/common.py` | `get_app()` `@st.cache_resource`; `incidents_df()`/`executions_df()` `@st.cache_data(ttl=60)`; `refresh()` clears cache after every write; `current_user()` = `st.user.user_name`, fallback `CURRENT_USER()` |
| `dashboard.py` | 5 KPIs, 2 charts, recent executions, walkthrough, confirmed reset (`CALL RESET_DEMO_DATA()`) |
| `incidents.py` | 4 filters, selectable table, detail (server, logs, metric charts, history), ingest form |
| `diagnosis.py` | mode radio (agent/direct/auto), run, metrics, root cause, policy, tools, evidence, runbooks, raw output, request approval |
| `approvals.py` | per pending request: evidence, live policy re-check, confirm checkbox gating Approve, comment, reject; history |
| `executions.py` | scenario selector, run, result banner, verification metrics, simulated output, history |
| `audit.py` | timeline with icons and summaries, action filter, raw JSON, export table |

## 12. Configuration management

| Setting | Where | Default |
|---|---|---|
| Object names, models, thresholds | env vars read by `core/config.py` | see §2.3 |
| Packages (deployed) | `environment.yml` | streamlit 1.52.2, pydantic 2.8.2, snowpark, snowflake.core, pandas |
| Packages (local tests) | `requirements.txt` | pydantic ≥2.8, snowpark ≥1.20, pytest ≥8 |
| Warehouse / runtime | `sql/05_streamlit.sql` | `COMPUTE_WH`, warehouse runtime |
| Theme | `.streamlit/config.toml` | light, primary `#1F6FEB` |

## 13. Logging and auditing

* **Audit**: every service action writes `AUDIT_LOGS` via `AuditService.record`. The repository has
  no update/delete for audit rows. 20 `AuditAction` values.
* **Python logging**: only `services/cortex_search_retriever.py` uses `logging.getLogger` (warnings
  on search fallback). A central logging configuration is **NOT IMPLEMENTED**.
* **Snowflake-side**: query history (`QUERY_ID`s returned in agent tool results) is available
  natively; no event table or telemetry is configured (**NOT IMPLEMENTED**).
