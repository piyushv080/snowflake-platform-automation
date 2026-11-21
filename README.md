# Snowflake Platform Automation

Snowflake run like any other infrastructure: desired state in Git, changes made by Ansible custom
modules through AWX, and **self-service requests from the ServiceNow catalog** — approved by a human,
executed automatically, and written back to the ticket.

```
ServiceNow catalog ──► approval ──► catalog task ──► AWX (via MID Server) ──► Ansible + iacforge.snowflake_iac ──► Snowflake
        ▲                                                                                                       │
        └──────────────────────────── task closed with the result (work notes) ◄────────────────────────────────┘
```

## What you can request today

| ServiceNow catalog | Fulfilled by | Result in Snowflake |
|---|---|---|
| **Snowflake Warehouse Request** | [`snowflake-iac/10_request_warehouse.yml`](snowflake-iac/10_request_warehouse.yml) | One cost-controlled `WH_REQ_*` warehouse with auto-suspend and a budget monitor |
| **Snowflake Access Request** | [`snowflake-iac/11_request_access.yml`](snowflake-iac/11_request_access.yml) | One standard role granted to or removed from one person |
| **Snowflake Onboarding** (order guide) | both of the above | Warehouse and access in one order, each with its own approval and job |

Guard rails are enforced in ServiceNow **and** again in Ansible: allowlisted sizes and roles, naming
standards, dev-only self-service, no admin roles, and requests can never change Git-managed objects or
users they did not create. Details: [`snowflake-iac/docs/REQUEST_FLOW.md`](snowflake-iac/docs/REQUEST_FLOW.md).

## Projects in this repository

| Project | Purpose | Documentation |
|---|---|---|
| Snowflake IAC | Ansible/AWX automation for provisioning, operating and self-service requests on Snowflake: GitOps converge, key rotation, AD user sync, monitoring, self-healing | [snowflake-iac/README.md](snowflake-iac/README.md) |
| OpsAgentX | Streamlit and Snowflake Cortex incident diagnosis with human-approved, simulated remediation | [opsagentx/README.md](opsagentx/README.md) |

Related repositories:

- [`iac_forge`](https://github.com/piyushv080/iac_forge) — the `iacforge.snowflake_iac` Ansible collection (custom Python modules)
- [`servicenow-platform-automation`](https://github.com/piyushv080/servicenow-platform-automation) — the ServiceNow catalog items, flows and actions (ServiceNow SDK / Fluent), deployed with GitHub Actions

## Development

Work on a focused branch from `dev`, for example `feature/opsagentx-ui` or `feature/iac-monitoring`, and merge it back into `dev` through a pull request. Keep `main` for reviewed, stable releases. Changes to one project should normally stay within its directory; the IAC GitHub Actions workflow runs only when `snowflake-iac/` changes.

Run each project from its own directory and follow its README for setup and deployment instructions.
