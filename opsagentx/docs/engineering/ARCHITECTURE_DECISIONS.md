# Architecture Decision Records

Format: decision · context · alternatives · consequences.

| # | Decision | Why | Alternatives considered | Trade-offs |
|---|---|---|---|---|
| ADR-1 | Dedicated `OPSAGENTX_ROLE` owning all objects | Least privilege; ACCOUNTADMIN only for setup | Run as ACCOUNTADMIN; SYSADMIN-owned objects | One more role to manage; the deploy CLI created the agent as ACCOUNTADMIN once (fixed) |
| ADR-2 | Ports and adapters (ABCs) for storage, LLM, agent, retrieval, integrations | Portability to the local Ollama/LangGraph version; fast offline tests | Call Snowpark/Cortex directly in services | More files; one composition root (`services/factory.py`) needed |
| ADR-3 | Pydantic for every boundary (input, LLM output) | Declarative validation and normalization; same in both projects | Manual checks; dataclasses | Pydantic 2.8.2 (Snowflake channel) vs 2.12.5 locally - code kept compatible |
| ADR-4 | `AI_COMPLETE` with JSON schema for direct mode | Schema-constrained output; enum restricts playbooks | `SNOWFLAKE.CORTEX.COMPLETE` (deprecated name), free text + regex | Still validated afterwards; prompt-size limits |
| ADR-5 | Cortex Search for runbooks, keyword fallback | Semantic retrieval (6/6 vs 5/6) with resilience | Keyword only; embeddings + VECTOR in a table | Service cost; 1-hour target lag |
| ADR-6 | `SEARCH_PREVIEW` as fallback query path | Python API blocked by sandbox TLS proxy | Disable TLS verification (rejected); REST | Documented for testing; higher latency |
| ADR-7 | Cortex Agent with generic tools backed by **SQL UDFs** | Read-only by construction; typed args; platform-managed orchestration | Python stored procedures; text-to-SQL tool (rejected: too broad) | UDFs duplicate some Python tool logic |
| ADR-8 | Call the agent via `DATA_AGENT_RUN` (SQL) | Reuses authenticated session; works in both Streamlit runtimes, procedures, sandbox | REST `agent:run` with streaming | No streaming; Snowflake recommends REST for most apps |
| ADR-9 | Python `PolicyEngine` is authoritative; SQL `TOOL_VALIDATE_REMEDIATION` advisory | Security must not depend on the model | Policy only in SQL; trust agent's self-check | Rules exist twice (both tested) |
| ADR-10 | Approval and execution are deterministic Python, never the agent | Never let an LLM execute infrastructure commands | Agent with an execute tool (rejected) | Less "autonomous" by design |
| ADR-11 | Simulated Ansible/ServiceNow behind interfaces | Safe demo; replaceable later | Real AAP in a lab | Real integration untested |
| ADR-12 | Verification from metrics, not job output | A job can succeed without fixing the problem | Trust job status | Needs a monitoring signal per category |
| ADR-13 | Streamlit **warehouse runtime** | pydantic from Anaconda channel without PyPI/EAI; agent reachable via SQL | Container runtime (needs artifact repo/EAI for pydantic) | Streamlit pinned to 1.52.2 |
| ADR-14 | `RESET_DEMO_DATA()` procedure as the single data definition | Script and UI reset stay identical | Separate SQL script + Python seeding | Truncates audit (demo only) |
| ADR-15 | Approver = `st.user.user_name` | Identity cannot be typed in | Text input (rejected) | Not yet verified in Snowflake |

## Performance and cost (measured)

| Operation | Latency | Tokens |
|---|---|---|
| Direct diagnosis | 13-19 s | ~0.7-2k prompt + ~150-400 completion |
| Agent diagnosis | 40-52 s | ~90k input (≈71k cache read) + ~1.6k output |
| Cortex Search query (`SEARCH_PREVIEW`) | 0.3-0.6 s (6.4 s first call) | - |
| Simulated playbook | 0.5 s (configurable) | - |

Agent mode is ~3x slower and uses far more input tokens; prefer direct mode for routine incidents
and the agent for unfamiliar ones, or use `auto`.

## Scalability, reliability, maintainability

* Scale: single-incident synchronous flow; no queue, no batch diagnosis, no concurrency control.
* Reliability: fallbacks for model, agent and search; no automatic retries; no transactions.
* Maintainability: 95 tests, clear seams; agent spec duplicated in YAML and `06_cortex_agent.sql`.

## Technical debt

1. Agent spec in two places (YAML and SQL file).
2. Policy rules in Python and SQL.
3. `SEARCH_PREVIEW` in the application path.
4. No transactions / atomic state updates.
5. No central logging; Python `logging` only in the search retriever.
6. Unused compute pool grant.
7. Live check scripts are manual, not CI.

## Recommended improvements

**Immediate**
1. Confirm the app inside Snowflake (startup, `st.user`, Python search API).
2. Enforce approver ≠ requester and an approver role.
3. Wrap each state transition in a transaction / conditional update.
4. Resource monitor + Cortex budget.
5. Generate `06_cortex_agent.sql` from the YAML (one source of truth).
6. Revoke the unused compute-pool grant (or move to container runtime deliberately).

**Long term**
1. Real AAP and ServiceNow clients via EAI + secrets, lab-only first.
2. Event-driven ingestion (webhook API / Snowpipe Streaming).
3. Agent evaluation dataset and regression scoring.
4. Threads/memory for follow-up questions in a chat UI.
5. Shared `opsagentx-core` package and the local integration plan.
6. Observability: event table, traces, cost dashboards.
