# OpsAgentX demo script

Start clean: **Dashboard → Reset demo data** (tick the confirmation, click Reset), or run
`CALL OPSAGENTX_DB.OPSAGENTX_SCHEMA.RESET_DEMO_DATA();`.

Role: `OPSAGENTX_ROLE`. App: `OPSAGENTX_DB.OPSAGENTX_SCHEMA.OPSAGENTX_APP`.

## Scenario 1 - INC1001: disk at 95% on server-01 (main demo)

| # | Page | Action | What to point out |
|---|---|---|---|
| 1 | Dashboard | Look at the KPIs | 8 incidents, 4 open (INC1001-INC1004), 2 critical |
| 2 | Incidents | Select INC1001 | Logs: *Disk usage crossed 90%*, */tmp grew 2.1 → 18.4 GB*, *app-api still running*; metrics chart shows disk 78 → 91.2 → 95% |
| 3 | AI Diagnosis | Mode **Cortex Agent**, click **Run AI diagnosis** (~45 s) | Root cause = /tmp growth; confidence ~0.9; playbook `disk_cleanup.yml`; **Policy: Allowed**; 7 tools including `analyze_disk_usage`; runbook RB-001 |
| 4 | AI Diagnosis | Click **Request approval for disk_cleanup.yml on server-01** | Policy is re-checked; incident becomes `PENDING_APPROVAL` |
| 5 | Approvals | Note **Approve is disabled**; tick the confirmation box, add a comment, click **Approve** | Your Snowflake user name is recorded as approver |
| 6 | Executions | Simulated outcome **Success**, click **Run simulated playbook** | Verification `disk_used_pct < 80.0`: **95 → 71.5**; incident `RESOLVED`; Ansible-style output |
| 7 | Audit | Select INC1001 | ~24 events: tool calls by `opsagentx-agent`, policy, approval, execution, verification, resolution, ServiceNow update |

Quick variant: use **Direct LLM** mode in step 3 (~15 s).

## Scenario 2 - INC1002: CPU at 96% on server-02 (test)

1. Diagnose (either mode). The agent usually follows the runbook and recommends the read-only
   `cpu_diagnostics.yml`; direct mode has recommended `restart_high_cpu_process.yml`.
2. Request approval → approve → run with **Success**.
3. With `cpu_diagnostics.yml`: the job succeeds but CPU is unchanged, so verification fails and
   the incident becomes `REMEDIATION_FAILED` ("diagnostics collected data but did not change the
   server; re-diagnose"). With `restart_high_cpu_process.yml`: CPU 96.5 → 34, `RESOLVED`.
4. Point out: **a successful job is not the same as a fixed incident.**

## Scenario 3 - INC1003: nginx stopped on server-03

1. Diagnose → `restart_service.yml`, runbook RB-004, no disk tool used.
2. Request approval, then on Approvals click **Reject** with reason
   "check nginx config manually first". Incident → `REJECTED`; execution → `CANCELLED`.
3. Point out that the rejected request never appears under Executions and cannot be run.
4. Optional: diagnose again, approve this time, run with **Success** → `service_up` 0 → 1, `RESOLVED`.

## Scenario 4 - INC1004: production database (policy block)

1. Diagnose. The recommendation is blocked: *R5: server 'prod-db-01' does not allow automated
   remediation* (the model also tends to recommend `none` because WAL growth is not a /tmp issue).
2. No **Request approval** button is offered; escalation is required.

## Failure paths to show (after approving any remediation)

On **Executions**, pick the simulated outcome before clicking Run:

| Outcome | Execution status | Incident status |
|---|---|---|
| Playbook failure | `FAILED` (`FAILED!` in output, no verification) | `REMEDIATION_FAILED` |
| Timeout | `TIMED_OUT` | `REMEDIATION_FAILED` |
| Runs but problem persists | `VERIFICATION_FAILED` (Ansible says `failed=0`, disk still 93.8%) | `REMEDIATION_FAILED` |

A failed incident can be re-diagnosed and retried; every attempt is a separate execution row.

## Ingest a new incident

Incidents → **Report a new incident**: try `inc2001`, source `servicenow`, server `Server-02`,
environment `prod`, severity `P1`, category `cpu`. It is normalized to
`INC2001 / server-02 / production / critical / high_cpu`. Try a server name like
`server-01; rm -rf /` to see validation reject it (and the rejection audited).
