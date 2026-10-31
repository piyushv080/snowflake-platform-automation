# Implementation Stages

> **Note on the stage count.** The brief asked for seven stages. The project was actually built in
> **nine** verified stages (the original plan in the requirements). For a seven-stage view:
> Stage A = 1, B = 2, C = 3+4, D = 5, E = 6, F = 7, G = 8+9. Each real stage is documented below.

**Why this order.** Each stage depends only on earlier ones and is testable on its own:
role/permissions before objects (1); data before code that reads it (2); pure business logic
before AI (3) so AI output has something trustworthy to be validated against; direct LLM (4)
before retrieval (5) so retrieval quality can be compared on a working pipeline; tools and policy
(4-5) before the agent (6) so the agent reuses tested capabilities; approval/execution (7) only
after diagnosis is trustworthy; UI (8) last, as a thin layer over tested services; tests and docs
(9) to close.

---

## Stage 1 - Environment inspection, role and project structure

| Item | Detail |
|---|---|
| Objective | Prove which Snowflake features work in this account before building on them |
| Business requirement | "Do not assume a feature is enabled"; least-privilege access |
| Concepts learned | RBAC, role hierarchy, database roles, cross-region inference, model availability, Anaconda channel |
| Snowflake services | Roles/grants, warehouses, compute pools, `INFORMATION_SCHEMA.PACKAGES` |
| Cortex capabilities | `AI_COMPLETE` availability probe |
| SQL/Python | `SHOW PARAMETERS`, `SHOW COMPUTE POOLS`, `GRANT`, `CURRENT_AVAILABLE_ROLES()`; Python venv |
| Files | `sql/00_role_setup.sql`, empty package folders |
| Objects | `OPSAGENTX_ROLE`; grants: CREATE DATABASE, USAGE on `COMPUTE_WH`, USAGE on `SYSTEM_COMPUTE_POOL_CPU`, `SNOWFLAKE.CORTEX_USER`; role granted to SYSADMIN and the developer user |
| Data flow | none |
| Depends on | ACCOUNTADMIN access |
| Tests | `SHOW GRANTS TO ROLE OPSAGENTX_ROLE`; `USE ROLE OPSAGENTX_ROLE; SELECT AI_COMPLETE('claude-sonnet-4-5', ...)` |
| Results | 4 grants confirmed; role returned `OK` from `claude-sonnet-4-5`; `llama3.3-70b` OK; `mistral-large2` rejected as legacy; `CORTEX_ENABLED_CROSS_REGION = ANY_REGION`; pydantic 2.8.2 in channel |
| Completion criteria | App role can call Cortex without ACCOUNTADMIN |
| Contribution | Security foundation; model choice |

Errors and fixes:

| Error | Cause | Fix |
|---|---|---|
| `SNOWFLAKE.CORTEX.COMPLETE` rejected by the tooling | `SNOWFLAKE.CORTEX.*` completion names are deprecated | Use `AI_COMPLETE` |
| `The model mistral-large2 has been in legacy state` | Model retired | Use `claude-sonnet-4-5` / `llama3.3-70b` |
| `This session does not have a current database` | `INFORMATION_SCHEMA.PACKAGES` unqualified | Use `SNOWFLAKE.INFORMATION_SCHEMA.PACKAGES` |
| `MAX(version)` reported snowpark 1.9.0 | String comparison of versions | Treat as unreliable; not a blocker |

## Stage 2 - Database, schema, tables and sample data

| Item | Detail |
|---|---|
| Objective | Persistent model of incidents, knowledge, allowlists and audit |
| Business requirement | Tables INCIDENTS ... AUDIT_LOGS; synthetic data incl. INC1001 |
| Concepts | Snowflake types (`VARIANT`, `ARRAY`, `TIMESTAMP_LTZ`), unenforced PK/FK, `AUTOINCREMENT`, `UUID_STRING()` |
| Services | Tables, (later) SQL stored procedure |
| SQL/Python | DDL, `INSERT ... SELECT` with `ARRAY_CONSTRUCT`, `$$` strings, `DATEADD`; `scripts/run_sql.py` with `execute_stream` |
| Files | `sql/01_setup.sql`, `sql/02_sample_data.sql`, `scripts/run_sql.py` |
| Objects | 9 tables (6 required + `SERVERS`, `PLAYBOOKS`, `SERVER_METRICS`) |
| Data flow | script → tables |
| Depends on | Stage 1 role |
| Tests | Row counts; INC1001 joined to its logs |
| Results | SERVERS 5, PLAYBOOKS 6, RUNBOOKS 6, INCIDENTS 8, LOGS 15, METRICS 14, AUDIT 4; INC1001's three specified log lines present |
| Completion | Reproducible, resettable demo data |
| Contribution | INC1004 (prod DB, automation disallowed) and draft RB-900 built in as negative test cases |

Later change (Stage 8): the data script became the `RESET_DEMO_DATA()` procedure. Errors then:
`unexpected 'COMMENT'` (fix: `COMMENT` before `EXECUTE AS OWNER`); runbook text indented by the
conversion (fix: strip indentation, verified with `SPLIT_PART`).

## Stage 3 - Python business logic and incident processing

| Item | Detail |
|---|---|
| Objective | Vendor-neutral domain, storage interfaces and incident lifecycle |
| Business requirement | Validate/normalize/store incidents; audit; portability |
| Concepts | Pydantic v2 validators, ABC ports/adapters, state machine, bind parameters |
| Services | Snowpark session |
| Files | `core/models.py`, `core/exceptions.py`, `core/config.py`, `database/*`, `services/incident_service.py`, `services/audit_service.py`, `tests/conftest.py`, `tests/test_incident_service.py`, `requirements.txt`, `scripts/check_stage3_snowflake.py` |
| Objects | none |
| Data flow | raw dict → `IncidentCreate` → `Incident` → INCIDENTS + AUDIT_LOGS |
| Depends on | Stage 2 tables |
| Tests | 17 unit tests on pydantic 2.12.5 and 2.8.2; live check (reads, ingest INC9001, invalid payload, lifecycle guard, audit) |
| Results | 17 passed (both versions); live: aliases normalized (`gh→github`, `qa→test`, `sev2→high`), `DIAGNOSED→RESOLVED` blocked |
| Completion | Services work against both repositories |
| Contribution | Everything later calls these services |

Errors: `cat >>` on the stage mount → `Operation not supported` (fix: use the editor);
`/tmp` wiped mid-session (fix: venv in `$HOME`).

## Stage 4 - Cortex AI diagnosis and structured output

| Item | Detail |
|---|---|
| Objective | Turn evidence into a validated, policy-checked diagnosis |
| Business requirement | Structured JSON; Pydantic validation; never trust the LLM |
| Concepts | Structured outputs, JSON schema enums, prompt/data separation, prompt injection, defense in depth |
| Cortex | `AI_COMPLETE(... response_format => PARSE_JSON(?), show_details => TRUE)` |
| Files | `llm/base.py`, `llm/snowflake_cortex.py`, `llm/schemas.py`, `core/security.py`, `agent/tools.py`, `agent/instructions.py`, `services/knowledge_service.py`, `services/diagnosis_service.py`, `tests/test_diagnosis.py`, `scripts/check_stage4_diagnosis.py` |
| Data flow | tools → prompt → AI_COMPLETE → `parse_diagnosis` → `_finalize` → `PolicyEngine` → INCIDENT_DIAGNOSIS |
| Depends on | Stage 3 |
| Tests | 33 new unit tests (50 total); live diagnosis of INC1001-INC1004 |
| Results | 50 passed; INC1001 `disk_cleanup.yml` 0.92 allowed; INC1002 `restart_high_cpu_process.yml` 0.92 allowed; INC1003 `restart_service.yml` 0.85 allowed; INC1004 `none` blocked (R1, R5); 13-17 s each |
| Completion | Malformed/invented answers never stored as valid |
| Contribution | Policy engine is reused by agent, approval and execution |

Probe that de-risked it: `AI_COMPLETE` with bind-parameter JSON schema returned
`structured_output[0].raw_message` matching the schema.

## Stage 5 - Runbook retrieval (Cortex Search)

| Item | Detail |
|---|---|
| Objective | Semantic retrieval of approved runbooks with a fallback |
| Concepts | Hybrid search, embeddings, reranker scores, attribute filters, change tracking, target lag |
| Cortex | `CREATE CORTEX SEARCH SERVICE`, `snowflake.core` API, `SEARCH_PREVIEW` |
| Files | `sql/03_cortex_search.sql`, `services/cortex_search_retriever.py`, `services/factory.py`, `tests/test_retrieval.py`, `scripts/check_stage5_search.py` |
| Objects | `RUNBOOK_SEARCH` (5 indexed rows, `snowflake-arctic-embed-m-v1.5`, `INCREMENTAL`) |
| Tests | 3 unit tests (53 total); keyword vs Cortex on 6 queries; re-run INC1001/INC1003 |
| Results | Cortex 6/6 top-1, keyword 5/6 ("processor saturated" missed by keyword); diagnoses now see only RB-001 / RB-004 |
| Completion | Retriever reports which path answered |
| Contribution | RAG grounding; same service becomes an agent tool |

Errors: Python API `SSLCertVerificationError ... self-signed certificate in certificate chain`
(sandbox proxy). Not worked around by disabling TLS; fell back to `SEARCH_PREVIEW`, which the docs
describe as for testing. The retriever stops retrying the Python API after the first failure.

## Stage 6 - Cortex Agent and controlled tools

| Item | Detail |
|---|---|
| Objective | Let an agent choose tools, without giving it unrestricted access |
| Concepts | Agent spec, generic tools backed by UDFs, tool allowlist, orchestration budget, tool trace parsing |
| Cortex | Cortex Agents (`CREATE AGENT ... FROM SPECIFICATION`), `DATA_AGENT_RUN` |
| Files | `sql/04_agent_tools.sql`, `agent/opsagentx_agent.yaml`, `agent/orchestration.py`, `services/diagnosis_service.py` (agent/auto modes), `tests/test_agent.py`, `tests/fixtures/agent_response_inc1003.json`, `scripts/check_stage6_agent.py`, `sql/06_cortex_agent.sql` (Stage 9) |
| Objects | 6 `TOOL_*` UDFs; `OPSAGENTX_DIAGNOSTIC_AGENT` |
| Tests | UDFs called directly; SQL policy mirror vs 6 cases; `cortex agents run`; 10 unit tests on a real response; live agent diagnosis of 4 incidents |
| Results | 63 passed; INC1001 7 tools `disk_cleanup.yml` 0.95; INC1002 `cpu_diagnostics.yml` 0.90 (followed runbook "diagnostics first"); INC1003 `restart_service.yml` 0.85; INC1004 `none` blocked; 45-52 s; ~90k input tokens (mostly cached) |
| Completion | Agent mode produces the same validated `Diagnosis` as direct mode |
| Contribution | Real agentic tool selection with platform-managed orchestration |

Errors and fixes:

| Error | Fix |
|---|---|
| `Declared return type 'VARIANT' is incompatible with actual return type 'OBJECT'` | `RETURNS OBJECT` |
| Agent created owned by ACCOUNTADMIN (CLI uses session role) | `GRANT OWNERSHIP ... TO ROLE OPSAGENTX_ROLE COPY CURRENT GRANTS`; later `06_cortex_agent.sql` creates it as `OPSAGENTX_ROLE` |
| `SHOW AGENTS` empty as OPSAGENTX_ROLE | Same ownership cause |
| CLI trace file not readable from shell | Captured the response via `DATA_AGENT_RUN` instead |

## Stage 7 - Human approval and simulated Ansible execution

| Item | Detail |
|---|---|
| Objective | Authorization and honest execution outcomes |
| Concepts | Human-in-the-loop, re-validation at each step, idempotent gate, verification from independent signal |
| Files | `integrations/ansible.py`, `integrations/servicenow.py`, `services/approval_service.py`, `services/remediation_service.py`, model + repository additions, `tests/test_remediation.py`, `scripts/check_stage7_lifecycle.py` |
| Tests | 28 unit tests; live scenarios |
| Results | 91 passed; live INC1001 → RESOLVED (95 → 71.5), INC1003 rejected and execution blocked, INC1002 simulated failure → REMEDIATION_FAILED, INC1004 request refused (R5); INC1001 audit trail 24 events in order |
| Completion | Rejected never runs; failed never reported as success |
| Contribution | The "act" half of the agentic workflow, safely |

Bug found live: verification `before` showed 78 (oldest sample). Fix: baseline = last sample before
job start; require a post-job sample. Two regression tests added.

## Stage 8 - Streamlit dashboard

| Item | Detail |
|---|---|
| Objective | Operator UI for the full lifecycle |
| Concepts | Streamlit multipage (`st.navigation`), caching, `st.user`, warehouse vs container runtime, `environment.yml`, AppTest |
| Files | `streamlit_app.py`, `app_pages/*` (6), `ui/common.py`, `environment.yml`, `.streamlit/config.toml`, `sql/05_streamlit.sql`, `scripts/deploy_streamlit.py`, `scripts/check_stage8_ui*.py`; `02_sample_data.sql` → procedure |
| Objects | `RESET_DEMO_DATA()`, `APP_STAGE`, `OPSAGENTX_APP` |
| Tests | AppTest render of 6 pages; AppTest click-through of INC1001 |
| Results | 6/6 pages no exception; lifecycle: diagnosis 92% `disk_cleanup.yml`, Approve disabled until confirmed, verification 95 → 71.5, dashboard 5 resolved / 3 open; `DESCRIBE STREAMLIT` shows user packages `streamlit==1.52.2, pydantic==2.8.2` |
| Not verified | App running inside Snowflake; `st.user` inside Snowflake |

Errors: sandbox `PYTHONPATH` shadowed streamlit 1.52.2 with 1.66 (fix: prepend venv site-packages);
`AppTest has no attribute 'segmented_control'` (fix: horizontal `st.radio`).

## Stage 9 - Tests, workflow verification, documentation

| Item | Detail |
|---|---|
| Files | `tests/test_lifecycle_demo.py`, `sql/06_cortex_agent.sql`, `README.md`, `docs/ARCHITECTURE.md`, `docs/DEMO.md`, `docs/PORTABILITY.md`, `docs/engineering/*` |
| Results | 95 passed; agent recreated via SQL as OPSAGENTX_ROLE, live run 39.8 s, 6 tools, RB-004 first |
