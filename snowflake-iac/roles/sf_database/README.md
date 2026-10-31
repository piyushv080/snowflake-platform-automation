# sf_database

Ensures databases and their schemas from `sf_databases`. Never drops schemas. Runs as `SYSADMIN`.
Module: `iacforge.snowflake_iac.snowflake_database`.

```yaml
sf_databases:
  - name: PLATFORM_OPS
    schemas: [MONITORING]
    data_retention_time_in_days: 1
```
