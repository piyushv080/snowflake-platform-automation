# servicenow_change

Used with `tasks_from`:

| tasks_from | Purpose |
|------------|---------|
| `gate_start` | require `cr_number` (format `CHG0001234`) for envs in `servicenow_require_cr_envs`; if `servicenow_enabled`, check the CR exists and is in `servicenow_allowed_states`, then add a "run started" work note |
| `gate_end` | work note with `sn_run_status` (SUCCESS / FAILED) |
| `incident` | create an incident (`servicenow.itsm.incident`) |

With `servicenow_enabled: false` (default) only the CR format is enforced and incidents are printed instead of created.
Connection: `servicenow_instance` (from vault) or `SN_HOST` / `SN_USERNAME` / `SN_PASSWORD` env vars.
