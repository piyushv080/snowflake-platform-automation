# AWX onboarding

You already run AWX locally. These steps connect it to this repository. Nothing here was run against your AWX.

## 1. Build and register the execution environment

AWX's default EE does not include `snowflake-connector-python`.

```bash
pip install ansible-builder
ansible-builder build -t snowflake-platform-ee:1.0 -f execution-environment.yml
# push to a registry AWX can pull from (or load into the local container runtime used by AWX)
```

## 2. Create the vault files and commit them encrypted

```bash
cp group_vars/env_dev/vault.yml.example group_vars/env_dev/vault.yml
ansible-vault encrypt group_vars/env_dev/vault.yml
git add group_vars/env_dev/vault.yml   # CI rejects it if it is not encrypted
```

## 3. Configure AWX

### Option A - as code (recommended)

```bash
ansible-galaxy collection install awx.awx
export CONTROLLER_HOST=https://<awx-host> CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD='...'
ansible-playbook awx/configure_awx.yml \
  -e awx_scm_url=https://github.com/<you>/snowflake-platform-automation.git \
  -e awx_ee_image=<registry>/snowflake-platform-ee:1.0 \
  -e awx_vault_password='<vault password>'
```

It creates: 2 credential types, a Vault credential, the EE, the Project, one inventory per environment
(sourced from `inventories/<env>/hosts.yml`), and one Job Template per playbook.

### Option B - in the UI

| AWX object | Value |
|------------|-------|
| Credential type | paste `credential_types/snowflake_keypair.yml` inputs/injectors (optional, alternative to vault) |
| Credential | type **Vault**, your vault password |
| Project | Git URL of this repo, branch `main`, *Update revision on launch* |
| Collections | `iacforge.snowflake_iac` is installed from the iac_forge GitHub repo on project sync (see `collections/requirements.yml`). The sync must be able to clone it: simplest is to make iac_forge public; for a private repo, check how your AWX version passes git credentials to collection installs, or bake the collection into the EE image |
| Inventory | *Snowflake Platform - dev* + Source *Sourced from a Project* -> `inventories/dev/hosts.yml` |
| Job Template | playbook e.g. `02_provision_platform.yml`, credential *Vault*, EE *snowflake-platform-ee* |
| Survey | `surveys/change_request.json` on templates that change things |

## 4. Schedules and webhooks

- **06 Monitoring**: schedule daily.
- **04 User Lifecycle**: schedule hourly or nightly.
- **02 Provision Platform**: enable the job template's GitHub webhook to run on merge to `main`,
  or let ServiceNow launch it (see `servicenow/`).

## 5. Event-Driven Ansible

With EDA Controller: create a Decision Environment containing this collection and `snowflake-connector-python`,
a Project pointing to this repo, and a Rulebook Activation for `rulebooks/snowflake_self_healing.yml`.
Change the rule actions to `run_job_template` targeting *SF - 07 Remediate (dev)*.

## Credentials: Vault vs AWX credential type

Both work because they set the same variable names (`sf_account`, `sf_user`, `sf_private_key`, ...).
Extra vars injected by the AWX credential override vault values. Pick one per environment.
