# Security and Reliability

Each control below lists **where it is implemented** and **how it was verified**. Gaps are listed
in §3 and are not hidden.

## 1. Implemented controls

| Area | Control | Where | Verified by |
|---|---|---|---|
| RBAC | Dedicated `OPSAGENTX_ROLE` owns every object (DB, schema, 9 tables, 6 UDFs, procedure, search service, agent, stages, Streamlit) | `sql/00_role_setup.sql`, all scripts run as the role | `SHOW GRANTS TO ROLE OPSAGENTX_ROLE` (28 grants, all ownership or the 4 setup grants) |
| Least privilege | Role has only: CREATE DATABASE, USAGE `COMPUTE_WH`, USAGE `SYSTEM_COMPUTE_POOL_CPU`, `SNOWFLAKE.CORTEX_USER`. ACCOUNTADMIN used only for role setup and one ownership transfer | same | same |
| Credentials | No passwords/keys in code. Inside Snowflake: embedded `st.connection`. Sandbox: OAuth token read at runtime from `SNOWFLAKE_TOKEN_FILE_PATH`, never written | `ui/common.py`, `database/connection.py`, `scripts/run_sql.py` | Secret scan (see TESTING) |
| Network | No external access integration, network rule or secret exists; no outbound calls; integrations are in-process simulators | account state | `SHOW EXTERNAL ACCESS INTEGRATIONS` empty (Stage 1) |
| SQL injection | All values bound (`params=[...]`); filter columns from a fixed list; UDFs have typed args, no dynamic SQL | `database/snowflake_repository.py`, `sql/04_agent_tools.sql` | code review; tests use injection-like strings |
| Input validation | Pydantic `IncidentCreate` (id pattern, server-name pattern blocking `;` `/` spaces, enums, length) | `core/models.py` | `test_invalid_values_are_rejected[server_name]` with `server-01; rm -rf /` |
| LLM output validation | JSON extraction + Pydantic `DiagnosisResult`; playbook must be a file name; confidence 0-1 | `llm/schemas.py` | 17 tests in `test_diagnosis.py` |
| LLM cannot relabel / self-approve | `_finalize` keeps real id/category; forces `requires_human_approval=True` | `services/diagnosis_service.py` | `test_model_cannot_waive_approval_or_relabel_incident` |
| Tool allowlisting | Agent spec lists exactly 7 tools; any other name in the trace → `LLMResponseError` | `agent/opsagentx_agent.yaml`, `agent/orchestration.py` | `test_agent_using_unknown_tool_is_refused` |
| Playbook allowlist | JSON-schema enum (direct) → name shape → PolicyEngine R1 (×4 call sites) → simulator refuses unknown | multiple | `test_unknown_playbook_is_rejected`, `test_simulator_refuses_unknown_playbooks` |
| Target allowlist | R4 inventory, R5 `ALLOW_AUTOMATION`, R6 environment spoofing | `core/security.py` | 3 tests + live INC1004 |
| Remediation authorization | Approval only by non-automated actor, explicit `confirmed=True`, PENDING only, policy re-check | `services/approval_service.py` | 8 tests |
| Approver identity | UI uses `st.user.user_name` (cannot be typed) | `ui/common.current_user` | **NOT VERIFIED inside Snowflake** (falls back to `CURRENT_USER()` in sandbox) |
| Rejected never runs | `CANCELLED` + gate requires APPROVED | approval + remediation services | `test_rejected_action_never_executes`, live INC1003 |
| Idempotent execution | Gate requires `NOT_STARTED`; one execution per approval | `RemediationService._check_gate` | `test_execution_cannot_run_twice` |
| Honest outcomes | FAILED / TIMED_OUT / exception / VERIFICATION_FAILED never resolve | `RemediationService.execute` | 5 tests + live INC1002 |
| Independent verification | Post-job metric vs threshold; no new metric → fail | `RemediationService.verify` | 2 regression tests |
| Audit | Every action audited; repository has no update/delete for audit | `services/audit_service.py` | `test_complete_lifecycle_audit_trail`, live 24-row trail |
| Lifecycle integrity | `ALLOWED_TRANSITIONS` state machine | `core/models.py` | 3 tests |
| Destructive UI action | Reset requires a confirmation checkbox | `app_pages/dashboard.py` | AppTest render |

## 2. Reliability

| Concern | Current behavior |
|---|---|
| LLM call failure | `SnowflakeCortexProvider` tries primary then fallback model, then `LLMProviderError` (audited) |
| Agent failure | `auto` mode falls back to direct mode (audited with `fallback`) |
| Search failure | Python API → `SEARCH_PREVIEW` → keyword retriever; the path used is recorded |
| Execution timeout | `AnsibleTimeoutError` → `TIMED_OUT`, incident `REMEDIATION_FAILED` |
| Recovery | `REJECTED` / `REMEDIATION_FAILED` → re-diagnose → new execution row |
| Retries | No automatic retry/backoff anywhere (**NOT IMPLEMENTED**); recovery is human-driven |
| Transactions | Multi-statement updates (execution + incident + audit) are **not** wrapped in a transaction (**gap**) |
| Concurrency | No locking; two users approving/running simultaneously could race (**gap**, mitigated by NOT_STARTED check, not atomic) |

## 3. Gaps and production risks (not hidden)

| # | Gap | Risk | Recommendation |
|---|---|---|---|
| 1 | Separation of duties not enforced: the requester can approve their own request | Insider/self-approval | Require approver ≠ requester; add an approver role check |
| 2 | No role-based authorization inside the app: anyone with `USAGE` on the Streamlit can approve | Over-broad approval rights | Map approval to a Snowflake role (`IS_ROLE_IN_SESSION`) or allowlist |
| 3 | App runs with owner's rights (`OPSAGENTX_ROLE`) for all viewers | Viewers act with owner privileges | Restrict app grants; consider caller's rights for reads |
| 4 | Audit append-only by convention only; the owner role can UPDATE/DELETE; `RESET_DEMO_DATA` truncates audit | Tampering | Separate audit schema/role, insert-only grants, no reset in prod |
| 5 | No transactions around state changes | Partial updates on failure | `BEGIN ... COMMIT` or a single stored procedure per transition |
| 6 | No concurrency control | Double execution under race | Conditional `UPDATE ... WHERE EXECUTION_STATUS='NOT_STARTED'` and check rows affected |
| 7 | `SEARCH_PREVIEW` used in app path | Latency, 300 kB cap, documented for testing | Use Python/REST API once confirmed in Snowflake |
| 8 | Agent output not schema-enforced | Format drift | Keep Pydantic; add evaluation set |
| 9 | Prompt injection via log text not tested adversarially | Misleading diagnosis | Red-team set; input scanning |
| 10 | No resource monitor / budget on `COMPUTE_WH` or Cortex usage | Cost overrun (agent ~90k tokens/run) | Resource monitor, Cortex budget, rate limit per user |
| 11 | Real integrations absent; when added they need secrets + EAI | Credential exposure | Snowflake `SECRET` + EAI with narrow network rules |
| 12 | Unused compute pool grant | Unneeded privilege | Revoke `USAGE ON COMPUTE POOL` if staying on warehouse runtime |
| 13 | Retention not configured | Compliance | Define retention for audit and diagnosis data |

## 4. Warehouse and resource usage

All SQL, UDF tools, search refresh and the Streamlit warehouse runtime use `COMPUTE_WH`.
Cortex usage is billed per token (agent orchestration, `AI_COMPLETE`) and per search service
serving/indexing. No resource monitor, Cortex budget or per-user quota is configured
(**NOT IMPLEMENTED**).
