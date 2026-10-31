# sf_resource_monitor

Ensures every entry in `sf_resource_monitors` exists with the given quota, frequency and triggers.
Runs as `ACCOUNTADMIN` (Snowflake requirement). Module: `iacforge.snowflake_iac.snowflake_resource_monitor`.

```yaml
sf_resource_monitors:
  - name: RM_PLATFORM_DEV
    credit_quota: 10
    notify_at: [75, 90]
    suspend_at: 100
    assign_to_account: false
```
