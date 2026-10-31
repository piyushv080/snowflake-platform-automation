# Portability guide: a local OpsAgentX with Ollama, LangGraph, FastAPI and SQLite/PostgreSQL

This guide explains how the Snowflake version was structured so a local version can reuse most of
it. **Nothing here is implemented yet**, and switching is **not automatic**: each adapter below must
be written and tested.

## 1. What is reusable vs Snowflake-specific

| Component | Files | Snowflake-specific? | Local plan |
|---|---|---|---|
| Domain models, enums, lifecycle state machine | `core/models.py` | No | reuse as-is |
| Exceptions, config | `core/exceptions.py`, `core/config.py` | No (config names objects) | reuse; add local settings (DB URL, Ollama URL) |
| Policy engine | `core/security.py` | No | reuse as-is |
| Diagnosis schema + defensive parser | `llm/schemas.py` | No | reuse as-is |
| Prompts / agent instructions | `agent/instructions.py` | No | reuse; may need tuning for smaller models |
| Diagnostic tools | `agent/tools.py` | No (uses repository + retriever ports) | reuse as LangGraph tool functions |
| Services | `services/incident,diagnosis,approval,remediation,audit,knowledge_service.py` | No | reuse as-is |
| Keyword runbook retriever | `services/knowledge_service.py` | No | reuse, or replace with a vector store |
| Ansible / ServiceNow simulators | `integrations/*.py` | No | reuse; add real AAP / ServiceNow clients |
| In-memory repository + tests | `database/memory_repository.py`, `tests/` | No | reuse; tests run unchanged |
| Snowflake repository | `database/snowflake_repository.py`, `database/connection.py` | **Yes** | replace with `SQLAlchemyRepository` |
| Cortex LLM adapter | `llm/snowflake_cortex.py` | **Yes** | replace with `OllamaProvider` |
| Cortex Search retriever | `services/cortex_search_retriever.py` | **Yes** | replace with local vector retriever |
| Cortex Agent runner | `agent/orchestration.py: CortexAgentRunner` | **Yes** | replace with `LangGraphAgentRunner` |
| SQL UDF agent tools, agent spec | `sql/04_agent_tools.sql`, `agent/opsagentx_agent.yaml` | **Yes** | not needed (tools are Python) |
| Composition root | `services/factory.py` | **Yes** | add `build_local_app()` |
| Streamlit pages | `app_pages/*`, `ui/common.py` | Mostly no | reuse; change `get_app()` and `current_user()` |

## 2. Interfaces that need a local implementation

| Interface (ABC) | Methods | Local class |
|---|---|---|
| `database.repositories.OpsRepository` (incidents, observability, inventory, knowledge, diagnosis, executions, audit) | ~20 small CRUD methods | `SQLAlchemyRepository` (SQLite for dev, PostgreSQL for shared use) |
| `llm.base.LLMProvider` | `generate_json(system, user, schema, ...)` | `OllamaProvider` |
| `services.knowledge_service.RunbookRetriever` | `search(query, category, limit)` | `KeywordRunbookRetriever` (exists) or `VectorRunbookRetriever` |
| `agent.orchestration.AgentRunner` | `run(incident_id) -> AgentRun` | `LangGraphAgentRunner` |
| `integrations.ansible.AnsibleClient` | `run_playbook(name, host, vars, timeout)` | `SimulatedAnsibleClient` (exists), later `AAPClient` |
| `integrations.servicenow.TicketClient` | `update_ticket(id, state, note)` | `SimulatedServiceNowClient` (exists), later `ServiceNowClient` |

Schema: translate `sql/01_setup.sql`. `VARIANT`/`ARRAY` become `JSON` (PostgreSQL `JSONB`,
SQLite `TEXT` + `json.dumps`); `UUID_STRING()` becomes `uuid.uuid4()` in Python; `TIMESTAMP_LTZ`
becomes `TIMESTAMPTZ` (store UTC). Unlike Snowflake, PostgreSQL enforces PK/FK; keep them.

## 3. Replacing Snowflake Cortex with Ollama

`OllamaProvider.generate_json` calls `POST {OLLAMA_URL}/api/chat` with
`{"model": ..., "messages": [{"role": "system", ...}, {"role": "user", ...}], "format": <json schema>,
"stream": false, "options": {"temperature": 0}}` and returns `LLMResponse(text=message.content, model=...)`.

Differences the adapter must handle (and that the existing code already defends against):

* **Schema adherence varies by model.** Ollama's `format` constrains output, but small models can
  still produce wrong values. `parse_diagnosis` + Pydantic + the policy engine are unchanged and still
  catch it.
* **Real system role.** Cortex's single-string `AI_COMPLETE` needs the system prompt inlined; Ollama
  has a separate system message.
* **Context length.** Local models may have small contexts; `DiagnosisService.build_prompt` already
  trims runbooks to 1,800 characters and logs to 20 lines - tune further if needed.
* **Errors / timeouts.** Map connection errors and HTTP errors to `LLMProviderError`.
* **No credentials** for local Ollama; for a remote one, read a token from the environment.

Start with a mid-size instruction model (e.g. a Llama 3.x 8B or Qwen 2.5 7B instruct variant),
then measure with the existing fake-LLM tests plus a small labeled set of incidents.

## 4. Replacing the Cortex Agent with LangGraph

The Cortex Agent does three things: plan which tool to call, call it, and write a final answer.
In LangGraph:

```
             ┌──────────────┐  tool calls   ┌────────────┐
  START ───▶ │  agent (LLM) │──────────────▶│ tools node │
             │ ChatOllama + │◀──────────────│ (ToolNode) │
             │ bound tools  │   results     └────────────┘
             └──────┬───────┘
                    │ no more tool calls
                    ▼
             ┌──────────────┐
             │ final answer │ ──▶ AgentRun(final_text, tool_calls, runbook_ids)
             └──────────────┘
```

* **Tools:** wrap the 7 `DiagnosticTools` methods with LangChain's `@tool` (or `StructuredTool`).
  They already validate arguments and are read-only. Do **not** add generic SQL or shell tools.
* **Model:** `ChatOllama(model=..., temperature=0)` with `.bind_tools(tools)`. Tool-calling quality
  depends heavily on the local model; check that it actually emits tool calls.
* **Guardrails the Snowflake version gets from the platform, which you must add:**
  a step limit (the Cortex budget was 90 s / 32k tokens) via `recursion_limit`; a per-tool timeout;
  and an allowlist check of tool names (`AgentRun.unexpected_tools` already enforces this downstream).
* **Return contract:** produce an `AgentRun` so `DiagnosisService._diagnose_with_agent` is unchanged
  (it still validates, cross-checks, runs policy, stores and audits).
* **Optional:** the approval step can become a LangGraph `interrupt` for human-in-the-loop, but keep
  `ApprovalService` as the authority. The graph may *pause* for approval; it must never *grant* it.

## 5. Replacing Cortex Search

* Simplest: keep `KeywordRunbookRetriever` (works today, lower quality - 5/6 top-1 vs 6/6 in Stage 5).
* Better: `VectorRunbookRetriever` using local embeddings (an Ollama embedding model or
  sentence-transformers) and pgvector / SQLite-vec / Chroma, plus keyword scoring for hybrid search.
  Index **approved runbooks only**, keep the `category` filter, and keep the score cut-off idea.

## 6. FastAPI service shape (suggested)

| Endpoint | Calls |
|---|---|
| `POST /incidents` | `IncidentService.ingest` (webhook for ServiceNow / monitoring) |
| `POST /incidents/{id}/diagnosis?mode=agent\|direct` | `DiagnosisService.diagnose` |
| `POST /incidents/{id}/approval-requests` | `ApprovalService.request_approval` |
| `POST /executions/{id}/approve` · `/reject` | `ApprovalService.approve` / `reject` (authenticated user only) |
| `POST /executions/{id}/run` | `RemediationService.execute` |
| `GET /incidents/{id}/audit` | `AuditService.timeline` |

Approver identity must come from authentication (OIDC/session), never a request field - the local
equivalent of `st.user.user_name`.

## 7. Suggested order of work

1. `SQLAlchemyRepository` + SQLite schema; run the existing tests against it (add a fixture).
2. `OllamaProvider`; run direct-mode diagnosis on the four demo incidents.
3. `build_local_app()` factory; point the Streamlit pages at it.
4. `LangGraphAgentRunner`; compare its tool choices and answers with the Cortex Agent results from Stage 6.
5. Vector retriever, FastAPI endpoints, then real AAP / ServiceNow clients behind feature flags.
