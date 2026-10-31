# OpsAgentX

AI-assisted infrastructure incident operations on Snowflake. OpsAgentX takes an
incident (disk full, CPU saturated, service down, ...), investigates it with a
Cortex Agent that may only use read-only tools, proposes an **allowlisted**
remediation playbook, validates it against a deterministic security policy,
waits for an explicit **human approval**, runs a **simulated** Ansible playbook,
verifies the result from metrics, and audits every step.

> The model proposes. Python policy decides what is allowed. A human approves.
> Nothing in this project touches real infrastructure: Ansible and ServiceNow are simulated.

## What is verified (and what is not)

| Capability | Status | How it was verified |
|---|---|---|
| Snowflake tables, sample data, `RESET_DEMO_DATA()` | Working | Created and queried as `OPSAGENTX_ROLE` |
| Cortex AI diagnosis (`AI_COMPLETE`, JSON schema output) | Working | Live runs on INC1001-INC1004 with `claude-sonnet-4-5` |
| Cortex Search over approved runbooks (`RUNBOOK_SEARCH`) | Working via `SEARCH_PREVIEW` | 6/6 top-1 on a semantic test set; Python API fails in the dev sandbox (TLS proxy), untested inside Snowflake |
| Cortex Agent with 7 controlled tools | Working | Live runs via `SNOWFLAKE.CORTEX.DATA_AGENT_RUN`, ~40-50 s per diagnosis |
| Policy engine, approval gate, simulated Ansible, verification | Working | 95 unit tests + live lifecycle runs in Snowflake |
| Streamlit app logic (all 6 pages, full lifecycle by clicking) | Working headless | Streamlit `AppTest` 1.52.2 against live Snowflake |
| Streamlit app **running inside Snowflake** (`OPSAGENTX_APP`) | Deployed, **not yet confirmed** | Object created with warehouse runtime and correct packages; needs to be opened in Snowsight |
| Cortex Agents **REST API** | Not used / not tested | The app calls the agent through SQL instead (see Architecture) |
| Real Ansible / ServiceNow / GitHub | Not implemented | Interfaces exist; only simulators are implemented |

## Prerequisites

* Snowflake account with Cortex AI, Cortex Search and Cortex Agents (verified here in `AWS_AP_NORTHEAST_1`
  with `CORTEX_ENABLED_CROSS_REGION = ANY_REGION`).
* A warehouse (scripts use `COMPUTE_WH`).
* `ACCOUNTADMIN` (or `SECURITYADMIN` + `SYSADMIN`) **once**, to create the app role.
* Model availability: `claude-sonnet-4-5` (primary) and `llama3.3-70b` (fallback) were verified.
  `mistral-large2` is legacy and rejected.

## Setup (in order)

| Step | File | Run as | Creates |
|---|---|---|---|
| 1 | `sql/00_role_setup.sql` | ACCOUNTADMIN | `OPSAGENTX_ROLE` + grants (CREATE DATABASE, warehouse, compute pool, `SNOWFLAKE.CORTEX_USER`) |
| 2 | `sql/01_setup.sql` | OPSAGENTX_ROLE | `OPSAGENTX_DB.OPSAGENTX_SCHEMA` and 9 tables |
| 3 | `sql/02_sample_data.sql` | OPSAGENTX_ROLE | `RESET_DEMO_DATA()` procedure and loads demo data |
| 4 | `sql/03_cortex_search.sql` | OPSAGENTX_ROLE | `RUNBOOK_SEARCH` Cortex Search service |
| 5 | `sql/04_agent_tools.sql` | OPSAGENTX_ROLE | 6 read-only SQL UDF agent tools |
| 6 | `sql/06_cortex_agent.sql` | OPSAGENTX_ROLE | `OPSAGENTX_DIAGNOSTIC_AGENT` (spec = `agent/opsagentx_agent.yaml`) |
| 7 | `python scripts/deploy_streamlit.py` | OPSAGENTX_ROLE | `APP_STAGE` + `OPSAGENTX_APP` (runs `sql/05_streamlit.sql`) |

Each SQL file can be run in a worksheet, or with the helper:

```bash
python scripts/run_sql.py sql/01_setup.sql            # defaults to --role OPSAGENTX_ROLE
python scripts/run_sql.py sql/00_role_setup.sql --role ACCOUNTADMIN
```

`scripts/run_sql.py` and `database/connection.py` read the Snowflake sandbox OAuth token from
`SNOWFLAKE_TOKEN_FILE_PATH`. No credentials are stored in this repository. Inside Snowflake the
app uses the embedded `st.connection("snowflake")` session.

If you are not the user in `00_role_setup.sql`, change `GRANT ROLE OPSAGENTX_ROLE TO USER ...`.

## Run the app

1. In Snowsight, switch your role to **OPSAGENTX_ROLE** (the app owner).
2. Open **Projects → Streamlit → OPSAGENTX_APP**. The first start installs packages from the
   Snowflake Anaconda channel and can take a minute or two.
3. Follow `docs/DEMO.md`.

To give other people access: `GRANT USAGE ON STREAMLIT OPSAGENTX_DB.OPSAGENTX_SCHEMA.OPSAGENTX_APP TO ROLE <role>;`
plus `USAGE` on the database and schema. The app runs with the owner's rights; the approver is
recorded as the **viewer's** Snowflake user name (`st.user.user_name`).

After code changes, run `python scripts/deploy_streamlit.py` again.

**Do not use the Workspace "Run" button on this folder.** It would start the *container* runtime,
which installs from `pyproject.toml`/PyPI. This account has no package repository or external
access integration configured, so `pydantic` would be missing. The deployed app uses the
*warehouse* runtime with `environment.yml` (Snowflake Anaconda channel).

## Tests

```bash
python3 -m venv ~/oax-venv && ~/oax-venv/bin/pip install -r requirements.txt
~/oax-venv/bin/python -m pytest tests -q          # 95 tests, no Snowflake connection, no cost
```

| Test file | Covers |
|---|---|
| `tests/test_incident_service.py` | valid parsing + normalization, invalid data, injection in server names, duplicates, lifecycle transitions, audit on create / on rejected input |
| `tests/test_diagnosis.py` | LLM JSON parsing (fences, prose), malformed output, missing fields, invalid values, policy rules R1-R8, unknown playbook, unauthorized server, environment spoofing, model errors |
| `tests/test_retrieval.py` | keyword fallback, draft runbook exclusion, Cortex Search fallback and score cut-off |
| `tests/test_agent.py` | parsing a **real** Cortex Agent response (fixture), agent/auto modes, fallback, non-allowlisted tool refusal |
| `tests/test_remediation.py` | approval confirmation, bots cannot approve, rejection never executes, policy re-checks, double execution, success / failure / timeout / verification failure, audit order |
| `tests/test_lifecycle_demo.py` | full lifecycle for INC1001, INC1002, INC1003, and the blocked INC1004 |

Live checks against Snowflake (they write data; reset with `CALL RESET_DEMO_DATA()` afterwards):

| Script | What it does |
|---|---|
| `scripts/check_stage3_snowflake.py` | repositories + incident service against real tables |
| `scripts/check_stage4_diagnosis.py` | direct-LLM diagnosis of the demo incidents |
| `scripts/check_stage5_search.py` | keyword vs Cortex Search comparison (read-only) |
| `scripts/check_stage6_agent.py` | Cortex Agent diagnosis of the demo incidents |
| `scripts/check_stage7_lifecycle.py` | approve / reject / fail / policy-block scenarios |
| `scripts/check_stage8_ui.py` | renders every Streamlit page headless (needs `streamlit==1.52.2`) |
| `scripts/check_stage8_ui_lifecycle.py` | clicks through INC1001 end-to-end in the UI |

## Project layout

```
opsagentx/
├── streamlit_app.py          Streamlit entry point (navigation)
├── app_pages/                dashboard, incidents, diagnosis, approvals, executions, audit
├── ui/common.py              session, cached reads, formatting helpers for pages
├── core/                     models (Pydantic), config, exceptions, security (policy engine)
├── database/                 repository interfaces, Snowflake + in-memory implementations
├── llm/                      LLMProvider interface, Cortex adapter, diagnosis schema + parser
├── agent/                    tools, instructions, Cortex Agent runner, agent spec YAML
├── integrations/             Ansible + ServiceNow interfaces and simulators
├── services/                 incident, diagnosis, approval, remediation, audit, knowledge, factory
├── sql/                      00-06 setup scripts
├── scripts/                  run_sql, deploy_streamlit, live check scripts
├── tests/                    pytest suite + real agent response fixture
├── docs/                     ARCHITECTURE.md, DEMO.md, PORTABILITY.md
├── environment.yml           warehouse-runtime packages (Snowflake Anaconda channel)
└── requirements.txt          local test dependencies
```

Runbooks live in `sql/02_sample_data.sql` (the `RUNBOOKS` inserts) rather than in separate files,
so the database is the single source of truth for Cortex Search.

## Known limitations

* **Agent latency and cost:** a Cortex Agent diagnosis takes ~40-50 s and ~90k input tokens (mostly
  cached); the direct LLM path takes ~15 s and ~2k tokens. Use direct mode for quick demos.
* **No streaming:** `DATA_AGENT_RUN` returns when the run ends, so the UI shows a spinner.
* **Agent output is not schema-constrained:** the agent is instructed to return JSON and the app
  validates it with Pydantic; `AI_COMPLETE` (direct mode) *is* schema-constrained.
* **Diagnostic playbooks never "resolve":** `cpu_diagnostics.yml` collects data only, so
  verification fails and the incident becomes `REMEDIATION_FAILED` with a note to re-diagnose.
* **Reset and Cortex Search:** `RESET_DEMO_DATA()` truncates and reloads `RUNBOOKS`; the search
  index catches up within its 1-hour target lag (content is identical).
* **Simulated integrations only.** See `docs/PORTABILITY.md` for how to plug in real AAP / ServiceNow.
