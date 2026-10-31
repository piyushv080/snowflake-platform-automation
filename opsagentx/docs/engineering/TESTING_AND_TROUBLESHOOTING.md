# Testing and Troubleshooting

## 1. Strategy

| Level | What | Where | Needs Snowflake |
|---|---|---|---|
| Unit | Services, policy, parsing, simulators against `InMemoryRepository` and `FakeLLM` | `tests/` | No |
| Contract | Agent parser against a **real** Cortex Agent response | `tests/fixtures/agent_response_inc1003.json`, `tests/test_agent.py` | No |
| Integration | Repositories, Cortex, search, agent, lifecycle against live objects | `scripts/check_stage3..7*.py` | Yes (writes data) |
| UI | Every page rendered; lifecycle clicked through with Streamlit `AppTest` | `scripts/check_stage8_ui*.py` | Yes |
| End-to-end | Demo scenarios in memory | `tests/test_lifecycle_demo.py` | No |

Test data: the `conftest._seed` fixture mirrors `RESET_DEMO_DATA` (5 servers, 4 playbooks, 4 runbooks
incl. draft RB-900, INC1001 + resolved INC0901, logs, metrics). Model answers come from `FakeLLM`
(scripted JSON / errors).

## 2. Commands

```bash
# unit + contract + e2e (no Snowflake)
python3 -m venv ~/oax-venv && ~/oax-venv/bin/pip install -r requirements.txt
~/oax-venv/bin/python -m pytest tests -q

# live checks (in an environment with a Snowflake session; reset afterwards)
python3 scripts/check_stage3_snowflake.py
python3 scripts/check_stage4_diagnosis.py INC1001 INC1002
python3 scripts/check_stage5_search.py
python3 scripts/check_stage6_agent.py INC1001
python3 scripts/check_stage7_lifecycle.py
python3 scripts/run_sql.py sql/02_sample_data.sql      # reset

# UI checks: need streamlit==1.52.2 first on the path
python3 -m venv --system-site-packages ~/oax-ui && ~/oax-ui/bin/pip install "streamlit==1.52.2" "pydantic==2.12.5" pytest
SP=$(~/oax-ui/bin/python -c "import sysconfig;print(sysconfig.get_paths()['purelib'])")
PYTHONPATH="$SP:$PYTHONPATH" ~/oax-ui/bin/python scripts/check_stage8_ui.py
PYTHONPATH="$SP:$PYTHONPATH" ~/oax-ui/bin/python scripts/check_stage8_ui_lifecycle.py
```

## 3. Results actually obtained (2026-10-31)

| Suite | Result |
|---|---|
| `pytest tests` | **95 passed**, 1 warning (third-party `anyio` plugin deprecation, unrelated) |
| Same suite on pydantic 2.8.2 (the deployed version) | **95 passed** (re-run during documentation, 2026-10-31) |
| `test_incident_service.py` | 17 |
| `test_diagnosis.py` | 33 |
| `test_retrieval.py` | 3 |
| `test_agent.py` | 10 |
| `test_remediation.py` | 28 |
| `test_lifecycle_demo.py` | 4 |
| Live Stage 3 | reads, ingest, invalid reject, lifecycle guard, audit - all as expected |
| Live Stage 4 | 4/4 diagnoses valid; INC1004 blocked |
| Live Stage 5 | Cortex Search 6/6, keyword 5/6 |
| Live Stage 6 | 4/4 agent diagnoses valid; 0 unexpected tools |
| Live Stage 7 | 4/4 scenarios as expected |
| UI render | 6/6 pages, 0 exceptions |
| UI lifecycle | INC1001 resolved via clicks; Approve disabled before confirmation |

**Not executed / not verified**: the app inside Snowflake;
Cortex Search Python API (fails in sandbox); REST Agents API; load/concurrency tests; adversarial
prompt-injection tests; real Ansible/ServiceNow.

## 4. Synthetic incident examples

```json
{"incident_id": "inc2001", "source": "ServiceNow", "server_name": " Server-02 ",
 "environment": "prod", "severity": "P1", "category": "cpu",
 "description": "CPU utilization above 90% for 20 minutes."}
```
→ normalized to `INC2001 / servicenow / server-02 / production / critical / high_cpu`.

Invalid: `"server_name": "server-01; rm -rf /"` → `IncidentValidationError: server_name: ... may contain only lowercase letters, digits, '.', '-'` and an `INCIDENT_REJECTED_INVALID` audit row with `incident_id=None`.

## 5. Troubleshooting (errors actually encountered)

| Symptom | Cause | Fix |
|---|---|---|
| `The model mistral-large2 has been in legacy state` | Retired model | Use `claude-sonnet-4-5` / `llama3.3-70b` (`OPSAGENTX_LLM_MODEL`) |
| `This session does not have a current database` | Unqualified `INFORMATION_SCHEMA` | Qualify, e.g. `SNOWFLAKE.INFORMATION_SCHEMA.PACKAGES` |
| `Declared return type 'VARIANT' is incompatible with actual return type 'OBJECT'` | SQL UDF return type | `RETURNS OBJECT` |
| `syntax error ... unexpected 'COMMENT'` in CREATE PROCEDURE | Clause order | `COMMENT` before `EXECUTE AS` |
| Agent invisible to `OPSAGENTX_ROLE` / `SHOW AGENTS` empty | Agent created by another role | Create via `sql/06_cortex_agent.sql` as `OPSAGENTX_ROLE`, or `GRANT OWNERSHIP ON AGENT ... TO ROLE OPSAGENTX_ROLE COPY CURRENT GRANTS` |
| `SSLCertVerificationError ... self-signed certificate` from `snowflake.core` | Sandbox TLS proxy | Retriever falls back to `SEARCH_PREVIEW`; do not disable TLS verification |
| `egress to this host is not allowed` (github.com) | Sandbox egress policy | Push from your own machine (see DEPLOYMENT_GUIDE) |
| `Operation not supported` on `cat >>` | Stage-mounted workspace does not support append | Rewrite the file |
| venv disappears | `/tmp` cleared | Create venvs under `$HOME` |
| Wrong Streamlit version in tests (1.66 instead of 1.52.2) | Sandbox `PYTHONPATH` precedence | Prepend the venv site-packages to `PYTHONPATH` |
| `AppTest has no attribute 'segmented_control'` | AppTest 1.52 limitation | Use `st.radio(horizontal=True)` |
| Verification "before" too low | Baseline took oldest sample | Fixed: last sample before job start |
| Search returns stale runbooks after reset | `TARGET_LAG = '1 hour'` | Wait, or `ALTER CORTEX SEARCH SERVICE RUNBOOK_SEARCH REFRESH` (not scripted) |
| App fails with missing `pydantic` | Started on container runtime (Workspace Run) | Use deployed `OPSAGENTX_APP` (warehouse runtime + `environment.yml`) |

## 6. Permission diagnosis procedure

1. `SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE();` - must be `OPSAGENTX_ROLE`, `COMPUTE_WH`.
2. `SHOW GRANTS TO ROLE OPSAGENTX_ROLE;` - expect the 4 setup grants + ownerships.
3. Cortex denied → `GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE OPSAGENTX_ROLE;` (ACCOUNTADMIN).
4. Viewer cannot open the app → `GRANT USAGE ON DATABASE OPSAGENTX_DB`, `ON SCHEMA ...`, `ON STREAMLIT ...OPSAGENTX_APP` to their role.
5. Any statement: `SELECT EXPLAIN_PRIVILEGES(statement => $$<sql>$$, missing_only => true);`
