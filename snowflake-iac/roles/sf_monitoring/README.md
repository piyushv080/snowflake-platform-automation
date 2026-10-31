# sf_monitoring

| Task file | What |
|-----------|------|
| `objects.yml` | `PLATFORM_OPS.MONITORING.PLATFORM_EVENTS` table + alerts `ALERT_CREDIT_SPIKE`, `ALERT_TASK_FAILED` (created only if missing, or with `sf_monitoring_replace_alerts=true`) |
| `cost_report.yml` | credits per warehouse from `ACCOUNT_USAGE` -> `reports/cost_report_<env>.html` and `.csv` |
| `hygiene.yml` | policy: auto-suspend <= `sf_monitoring_max_auto_suspend`, used in last `sf_monitoring_idle_days`, resource monitor attached |

Alerts are owned by `PLATFORM_AUTOMATION_ROLE` (`EXECUTE ALERT`, `EXECUTE MANAGED ALERT`, `MONITOR USAGE` from `sf_bootstrap`).
They are **serverless** by default (`sf_monitoring_alert_warehouse: ""`), so no warehouse is resumed every run;
set a warehouse name to run them on a warehouse instead.
`ACCOUNT_USAGE` data can lag by up to a few hours; alerts use `INFORMATION_SCHEMA` table functions for lower latency.
Dollar estimates only appear when `sf_monitoring_price_per_credit` is set to your contract rate.
