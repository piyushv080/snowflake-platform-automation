# Deployment and Reproduction Guide

No credentials appear in this guide or the repository. Inside Snowflake, authentication is the
Snowsight session. The helper scripts read an OAuth token from `SNOWFLAKE_TOKEN_FILE_PATH`
(provided by the Snowflake sandbox); outside it, run the SQL files in a worksheet instead.

## 1. Prerequisites

| Requirement | Verified value |
|---|---|
| Snowflake account with Cortex AI, Cortex Search, Cortex Agents | `XW63202`, `AWS_AP_NORTHEAST_1` |
| Cross-region inference (if models are not local) | `CORTEX_ENABLED_CROSS_REGION = ANY_REGION` |
| Models | `claude-sonnet-4-5`, `llama3.3-70b` |
| Warehouse | `COMPUTE_WH` (any size; XS is enough for the demo) |
| Admin access once | ACCOUNTADMIN, or SECURITYADMIN + SYSADMIN |
| Python 3.11/3.12 locally (tests only) | 3.12.15 used |

Check model access before starting:

```sql
SELECT AI_COMPLETE('claude-sonnet-4-5', 'Reply with OK');
SHOW PARAMETERS LIKE 'CORTEX_ENABLED_CROSS_REGION' IN ACCOUNT;
```

## 2. Roles and permissions (`sql/00_role_setup.sql`, run as ACCOUNTADMIN)

```sql
CREATE ROLE IF NOT EXISTS OPSAGENTX_ROLE;
GRANT ROLE OPSAGENTX_ROLE TO ROLE SYSADMIN;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE OPSAGENTX_ROLE;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE OPSAGENTX_ROLE;
GRANT USAGE ON COMPUTE POOL SYSTEM_COMPUTE_POOL_CPU TO ROLE OPSAGENTX_ROLE;   -- optional (unused by warehouse runtime)
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE OPSAGENTX_ROLE;
GRANT ROLE OPSAGENTX_ROLE TO USER <your_user>;   -- edit the user name in the file
```

## 3. Execution order (everything after step 1 as `OPSAGENTX_ROLE`)

| # | Command | Result |
|---|---|---|
| 1 | `python scripts/run_sql.py sql/00_role_setup.sql --role ACCOUNTADMIN` | role + grants |
| 2 | `python scripts/run_sql.py sql/01_setup.sql` | `OPSAGENTX_DB.OPSAGENTX_SCHEMA`, 9 tables |
| 3 | `python scripts/run_sql.py sql/02_sample_data.sql` | `RESET_DEMO_DATA()` created and called |
| 4 | `python scripts/run_sql.py sql/03_cortex_search.sql` | change tracking + `RUNBOOK_SEARCH` |
| 5 | `python scripts/run_sql.py sql/04_agent_tools.sql` | 6 `TOOL_*` UDFs |
| 6 | `python scripts/run_sql.py sql/06_cortex_agent.sql` | `OPSAGENTX_DIAGNOSTIC_AGENT` |
| 7 | `python scripts/deploy_streamlit.py` | `APP_STAGE`, 41 files uploaded, `OPSAGENTX_APP` |

Order matters: 4 needs RUNBOOKS (2); 6 needs the UDFs (5) and the search service (4); 7 needs everything.
`05_streamlit.sql` is executed by `deploy_streamlit.py` after the upload (it must run after the files are on the stage).

Without the helper scripts, paste each SQL file into a worksheet (role `OPSAGENTX_ROLE`). For
step 7, upload the app files to `@OPSAGENTX_DB.OPSAGENTX_SCHEMA.APP_STAGE/opsagentx/` preserving
folders (`streamlit_app.py`, `environment.yml`, `.streamlit/config.toml`, and the `.py` files in
`core database llm agent integrations services ui app_pages`), then run `sql/05_streamlit.sql`.

## 4. Verify each step

```sql
USE ROLE OPSAGENTX_ROLE;
SELECT COUNT(*) FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.INCIDENTS;                 -- 8
SHOW CORTEX SEARCH SERVICES IN SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA;          -- RUNBOOK_SEARCH ACTIVE, 5 rows
SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW('OPSAGENTX_DB.OPSAGENTX_SCHEMA.RUNBOOK_SEARCH',
  '{"query": "filesystem almost full", "columns": ["RUNBOOK_ID"], "limit": 1}'); -- RB-001
SELECT OPSAGENTX_DB.OPSAGENTX_SCHEMA.TOOL_ANALYZE_DISK_USAGE('server-01');    -- 95% critical
SHOW AGENTS IN SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA;                          -- owner OPSAGENTX_ROLE
DESCRIBE STREAMLIT OPSAGENTX_DB.OPSAGENTX_SCHEMA.OPSAGENTX_APP;               -- SYSTEM$WAREHOUSE_RUNTIME, pydantic==2.8.2
```

Agent smoke test:

```sql
SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN('OPSAGENTX_DB.OPSAGENTX_SCHEMA.OPSAGENTX_DIAGNOSTIC_AGENT',
  $${"messages":[{"role":"user","content":[{"type":"text","text":"Diagnose incident INC1001."}]}],"stream":false}$$);
```

## 5. Python environment and packages

| Context | Packages | Source |
|---|---|---|
| Deployed app | `python=3.11, streamlit=1.52.2, pydantic=2.8.2, snowflake-snowpark-python, snowflake.core, pandas` | `environment.yml` → Snowflake Anaconda channel (no PyPI, no EAI) |
| Local tests | `pydantic>=2.8,<3`, `snowflake-snowpark-python>=1.20`, `pytest>=8` | `requirements.txt` |

Do **not** start this folder with the Workspace "Run" button: that uses the container runtime and
PyPI, where `pydantic` is not available without an artifact repository or external access integration.

## 6. Integrations

None to configure: Ansible and ServiceNow are simulated in-process. For real integrations (future):
create a `SECRET`, a `NETWORK RULE` for the AAP/ServiceNow host, an `EXTERNAL ACCESS INTEGRATION`,
attach it to the Streamlit (`EXTERNAL_ACCESS_INTEGRATIONS`, `SECRETS`), and implement
`AAPClient` / `ServiceNowClient` behind the existing interfaces. **NOT IMPLEMENTED.**

## 7. Launch

1. Snowsight → role `OPSAGENTX_ROLE`.
2. Projects → Streamlit → `OPSAGENTX_APP` (first start installs packages).
3. To share: `GRANT USAGE ON DATABASE OPSAGENTX_DB TO ROLE <r>; GRANT USAGE ON SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA TO ROLE <r>; GRANT USAGE ON STREAMLIT OPSAGENTX_DB.OPSAGENTX_SCHEMA.OPSAGENTX_APP TO ROLE <r>;`

## 8. Tests

See [TESTING_AND_TROUBLESHOOTING.md](TESTING_AND_TROUBLESHOOTING.md) §2.

## 9. Validate the full workflow

Follow `docs/DEMO.md` Scenario 1, then:

```sql
SELECT INCIDENT_ID, STATUS FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.INCIDENTS WHERE INCIDENT_ID = 'INC1001';  -- RESOLVED
SELECT EXECUTION_STATUS, VERIFICATION_RESULT FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.REMEDIATION_EXECUTIONS
 WHERE INCIDENT_ID = 'INC1001';                                                                         -- SUCCEEDED, passed true
SELECT ACTION, ACTOR, CREATED_AT FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.AUDIT_LOGS
 WHERE INCIDENT_ID = 'INC1001' ORDER BY CREATED_AT;                                                     -- full timeline
```

Reset: `CALL OPSAGENTX_DB.OPSAGENTX_SCHEMA.RESET_DEMO_DATA();`

## 10. Updating and tearing down

* Code change → `python scripts/deploy_streamlit.py`.
* Agent change → edit `agent/opsagentx_agent.yaml`, copy into `sql/06_cortex_agent.sql`, run it.
* Teardown (ACCOUNTADMIN or the role): `DROP DATABASE OPSAGENTX_DB;` then `DROP ROLE OPSAGENTX_ROLE;`.

## 11. Publishing to GitHub

The development sandbox cannot reach github.com, so the repository is prepared locally and pushed
from your machine:

```bash
git clone opsagentx.bundle opsagentx      # or unzip opsagentx.zip, which contains .git
cd opsagentx
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

`.gitignore` excludes virtual environments, caches, `.streamlit/secrets.toml`, keys and `.env` files.
