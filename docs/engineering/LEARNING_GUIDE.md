# Engineering Learning Guide

For each concept: **what** it is · **why** it matters · **how we used it** · **real example** ·
**what you should be able to explain**.

## Snowflake fundamentals

| Concept | Explanation | In OpsAgentX | Interview takeaway |
|---|---|---|---|
| Role-based access control | Privileges go to roles; users get roles; roles form hierarchies | `OPSAGENTX_ROLE` owns everything; granted to SYSADMIN | "Ownership role vs usage grants; why not ACCOUNTADMIN" |
| Database roles | Roles scoped to a database; Snowflake ships `SNOWFLAKE.CORTEX_USER` | Granted to unlock Cortex | "How do you give an app Cortex access?" |
| Warehouses | Compute for SQL; billed while running | `COMPUTE_WH` runs SQL, UDF tools, search refresh, Streamlit | "Which workloads consume warehouse credits here?" |
| Unenforced constraints | PK/FK declared, not enforced (only NOT NULL) | Services enforce uniqueness and lifecycle | "Where do you enforce integrity in Snowflake?" |
| Semi-structured data | `VARIANT`, `ARRAY`, `OBJECT`, `PARSE_JSON` | `AUDIT_LOGS.DETAILS`, `VERIFICATION_RESULT`, `TOOLS_USED` | "Why INSERT ... SELECT PARSE_JSON(?) not VALUES" |
| Stages | File storage for code/data | `APP_STAGE` holds app source | "How does a Streamlit app get its code?" |

## SQL and data engineering

* **Bind parameters** - values never concatenated. Example: `session.sql("... WHERE INCIDENT_ID = ?", params=[incident_id])`. Explain SQL injection and why identifiers need allowlists.
* **SQL UDFs returning OBJECT** - `TOOL_ANALYZE_DISK_USAGE` uses CTEs, `MAX_BY`/`MIN_BY`, `OBJECT_CONSTRUCT`. Error learned: declared VARIANT vs actual OBJECT.
* **Stored procedures (SQL scripting)** - `RESET_DEMO_DATA()`: `BEGIN ... END`, `EXECUTE AS OWNER`, clause order matters.
* **Change tracking** - required by Cortex Search incremental refresh.

## Snowpark Python

* **What**: Python API to Snowflake sessions/DataFrames. **Here**: only as a session for parameterized SQL (`database/snowflake_repository.py`). `get_active_session()` inside Snowflake; `st.connection("snowflake").session()` in Streamlit.
* **Explain**: difference between client-side Snowpark (sandbox), Streamlit-provided session, and stored-procedure session.

## Cortex AI

* **AI_COMPLETE** - LLM call in SQL. We pass `model`, `prompt`, `model_parameters` (temperature 0), `response_format` (JSON schema), `show_details` (usage). Example output path: `structured_output[0].raw_message`.
* **Model availability and cross-region inference** - verify before use (mistral-large2 was retired).
* **Explain**: cost per token, model fallback, why deprecated `SNOWFLAKE.CORTEX.COMPLETE` was avoided.

## Prompt engineering

* Separate instructions from data (`=== INPUT ===`), state rules explicitly (allowed playbooks, no shell commands, always require approval), tell the model to ignore instructions inside data.
* Example: `agent/instructions.py: DIAGNOSIS_SYSTEM_PROMPT`; agent `instructions.orchestration` 7-step plan.
* Explain: prompts are guidance, not security.

## Structured outputs

* JSON schema with enums constrains generation; Pydantic validates after.
* Example: `diagnosis_json_schema(["disk_cleanup.yml", ...])` → enum + `"none"`; `parse_diagnosis` rejects `"rm -rf /tmp/*"`.
* Explain: "constrained ≠ correct" - the policy engine still checks.

## RAG and retrieval

* Retrieve relevant documents and put them in the prompt so answers are grounded.
* Example: INC1001 prompt includes RB-001, which names `disk_cleanup.yml` as approved remediation.
* Explain: why only approved runbooks are indexed (draft RB-900 says `rm -rf`).

## Cortex Search

* Managed hybrid (vector + keyword) search with reranking; `ATTRIBUTES` for filters; `TARGET_LAG`.
* Example: "processor saturated after deploy" → RB-002 (keyword search missed it).
* Explain: reranker score cut-off, `SEARCH_PREVIEW` vs Python/REST API.

## Cortex Agents and tool calling

* An agent spec = model + instructions + tools (+ resources). Tools: `generic` (UDF/procedure), `cortex_search`, `cortex_analyst_text_to_sql`, ...
* Example: 6 generic tools with `input_schema` + `tool_resources.identifier` + warehouse; 1 search tool.
* Tool calling: the model emits `tool_use {name, input}`, platform runs it, returns `tool_result`.
* Explain: why no text-to-SQL tool (too broad), how we parse the trace, how we refuse unknown tools.

## Agent orchestration

* Agent mode (model plans) vs pipeline mode (code plans) vs auto (fallback).
* Explain the split "agentic diagnosis, deterministic execution", the budget (90 s / 32k), and the observed latency/token cost.

## API integration

* `DATA_AGENT_RUN` (SQL wrapper of the Agents Run API) vs REST with streaming; Cortex Search Python API vs `SEARCH_PREVIEW`.
* Integration interfaces (`AnsibleClient`, `TicketClient`) and simulators; what a real AAP call would be (`POST /api/v2/job_templates/{id}/launch/`).

## Security and authorization

* Defense in depth (schema enum → Pydantic → cross-checks → policy ×4 → human → gate → simulator).
* Human-in-the-loop: explicit confirmation, non-bot approver, rejection reason.
* Explain the gaps honestly: no separation of duties, owner's-rights app, mutable audit.

## Testing and debugging

* Fakes over mocks: `InMemoryRepository`, `FakeLLM`, `FixtureAgent`.
* Contract test from a real agent response.
* Live check scripts and headless UI with Streamlit `AppTest`.
* Real bugs caught by live runs: verification baseline; Streamlit version shadowing.

## Deployment and operations

* Streamlit warehouse vs container runtime; `environment.yml` vs `pyproject.toml`; `RUNTIME_NAME`.
* Idempotent DDL (`IF NOT EXISTS`, `CREATE OR REPLACE`), ordered setup scripts, demo reset procedure.

## Self-check questions

1. Why can't the agent delete a file even if prompted to?
2. Walk through what happens if the model returns `"recommended_playbook": "wipe_disk.yml"`.
3. Why is a successful Ansible job not enough to resolve an incident?
4. What would you change to let two approvers be required for production?
5. How would you replace Cortex with Ollama without touching `DiagnosisService`?
6. Where are the three places a playbook name is validated before execution, and why three?
