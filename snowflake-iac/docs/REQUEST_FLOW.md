# Snowflake Self-Service Requests — ServiceNow → MID Server → AWX → Snowflake

| Catalog item | Playbook | AWX job template |
|---|---|---|
| Snowflake Warehouse Request | `10_request_warehouse.yml` | SF - 10 Request Warehouse |
| Snowflake Access Request | `11_request_access.yml` | SF - 11 Request Access |

Both items can be ordered together through the **Snowflake Onboarding** order guide in ServiceNow.

## Warehouse request

Self-service Snowflake warehouses from the ServiceNow catalog, fulfilled by AWX.

```
Requester ─► Catalog item "Snowflake Warehouse Request" (Fluent, servicenow-platform-automation repo)
              │  validation subflow + approval (Catalog Toolkit)
              ▼
           SCTASK created ─► Flow action "Launch AWX Job" ──(REST via MID Server)──► AWX on laptop
                                                                                     │
                                       job template "SF - 10 Request Warehouse (dev)"│
                                                                                     ▼
                                               snowflake-iac/10_request_warehouse.yml
                                               1. SCTASK -> Work in Progress
                                               2. policy check (group_vars/all/request_policy.yml)
                                               3. iacforge.snowflake_iac.snowflake_warehouse
                                               4. SCTASK -> Closed Complete / Incomplete + work notes
                                                                                     │
           RITM synced by the Catalog Toolkit business rule ◄── ServiceNow REST API ◄┘
```

## Who does what

| Layer | Responsibility | Where |
|---|---|---|
| ServiceNow | Intake form, approval, SCTASK, launch AWX through the MID Server, show results | `servicenow-platform-automation` repo (`catalog-toolkit/`) |
| MID Server (laptop) | Lets the cloud PDI reach the private AWX API — AWX is never exposed to the internet | ServiceNow MID Server install |
| AWX | Runs only the approved job template, holds all secrets as credentials | `awx/` |
| Ansible | Enforces policy again, changes Snowflake, reports back to the SCTASK | this folder |
| GitHub Actions | CI only: lint, syntax, vault-encryption and secret checks | `.github/workflows/snowflake-iac.yml` |

## Policy (enforced in Ansible, not only in the form)

- Name must match `^WH_REQ_[A-Z0-9_]{3,30}$` — requests can never touch Git-managed warehouses.
- Size: `XSMALL` or `SMALL`. Auto-suspend: 60–600 s. Resource monitor from an allowlist.
- Requested environment must equal the inventory's `sf_env`, and only `dev` allows self-service.
- ServiceNow identifiers are validated (`RITM#######`, `SCTASK#######`, 32-char sys_id).

## AWX setup (manual parts)

1. Build the execution environment: `cd snowflake-iac && ansible-builder build -t snowflake-platform-ee:1.0 -f execution-environment.yml`
2. Create credentials in AWX by hand (recommended over the committed vault for a public repo):
   - `Snowflake Key Pair - dev` (type *Snowflake Key Pair*)
   - `ServiceNow - PDI` (type *ServiceNow ITSM*) — use a dedicated integration user with sc_task write access, not admin
3. Run `ansible-playbook snowflake-iac/awx/configure_awx.yml` with `awx_scm_url` pointing at this repo
   (`awx_playbook_dir: snowflake-iac` is the default, because the AWX project root is the repo root).
4. Create an AWX OAuth token with *write* scope for the ServiceNow integration user.

## Local test (no ServiceNow, no Snowflake changes)

```bash
cd snowflake-iac
ansible-galaxy collection install -r collections/requirements.yml -p ./collections
ansible-playbook 10_request_warehouse.yml -e @examples/request_warehouse.yml --check
# policy rejection example:
ansible-playbook 10_request_warehouse.yml -e @examples/request_warehouse.yml -e request_wh_size=X4LARGE --check
```

## Status

| Piece | Status |
|---|---|
| `10_request_warehouse.yml`, `servicenow_sctask` role, policy | Done; syntax + ansible-lint (production) pass; policy rejection paths tested locally |
| AWX job template as code | Done (`awx/job_templates.yml`), not yet applied to a live AWX |
| Live run against Snowflake | Not yet run |
| ServiceNow catalog item, Flow action, MID Server | Next |
| v2: request opens a PR to `platform.yml` (GitOps) instead of direct create | Planned |

## Access request (`11_request_access.yml`)

- Grants or revokes **one** role for **one** person; only that role is touched (`roles_scope`).
- Roles: `PLATFORM_READER`, `PLATFORM_ENGINEER` only — never admin roles.
- New users are created as `PERSON` with comment `managed-by:servicenow-request`, default role and warehouse.
- An existing user is changed **only** if it carries that comment. Admins, service users and AD-synced users
  (`managed-by:ad-sync`) are refused. The automation service user is always protected.
- Revoke on a user that does not exist succeeds with "nothing to revoke".
- Authentication for new users (SSO, key pair) is outside this request.

```bash
ansible-playbook 11_request_access.yml -e @examples/request_access.yml --check
```
