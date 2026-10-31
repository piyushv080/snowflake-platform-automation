# sf_remediation

Actions triggered by Event-Driven Ansible through `07_remediate.yml`.

| `remediation_action` | Inputs | Action |
|----------------------|--------|--------|
| `suspend_warehouse` | `wh_name`, `credits_last_hour` | `snowflake_warehouse state=suspended` (skipped for `sf_remediation_protected_warehouses`) + incident |
| `retry_task` | `task_fqn`, `task_error` | `EXECUTE TASK` once + incident |

All inputs are validated against identifier regexes before use.

**Scope safety:** a warehouse is only auto-suspended if it is declared in `sf_warehouses` (managed by this project)
and not listed in `sf_remediation_protected_warehouses`. Credit spikes on any other warehouse (other teams,
interactive users) only raise a high-urgency incident.
