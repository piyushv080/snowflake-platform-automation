# sf_warehouse

Ensures every entry in `sf_warehouses` (size, auto-suspend, auto-resume, monitor, comment, state).
Runs as `SYSADMIN`. Module: `iacforge.snowflake_iac.snowflake_warehouse`.

```yaml
sf_warehouses:
  - name: WH_ANALYTICS_DEV
    size: XSMALL
    auto_suspend: 60
    resource_monitor: RM_PLATFORM_DEV
    state: present        # present | absent | suspended | resumed
```

Omitted properties are not managed on existing warehouses.
