# sf_bootstrap

One-time creation of the automation identity. Runs with a **human admin key** (`vault_sf_admin_*`), see `01_bootstrap.yml`.

Creates:
- `PLATFORM_AUTOMATION_ROLE` inheriting `sf_bootstrap_inherited_roles` and granted to ACCOUNTADMIN
  (not to SYSADMIN - that would be a circular grant)
- account privileges `EXECUTE ALERT`, `EXECUTE MANAGED ALERT`, `EXECUTE TASK`, `MONITOR USAGE`;
  `IMPORTED PRIVILEGES` on `SNOWFLAKE` (cost views)
- service user `SVC_PLATFORM_AUTOMATION` (TYPE=SERVICE, key-pair only) with a generated encrypted key

Re-running is safe: if the service user already exists its key is **never** replaced
(use `05_rotate_keys.yml` for that), and every task reports `changed=0`.

| Variable | Default |
|----------|---------|
| `sf_bootstrap_service_user` | `SVC_PLATFORM_AUTOMATION` |
| `sf_bootstrap_key_path` | `{{ playbook_dir }}/.keys/<user>.p8` (git-ignored) |
| `sf_bootstrap_inherited_roles` | `[SYSADMIN, SECURITYADMIN]` (demo scope) |
| `sf_bootstrap_grant_accountadmin` | `true` (needed for resource monitors) |

After the run, copy the key into Ansible Vault and delete the local file.
