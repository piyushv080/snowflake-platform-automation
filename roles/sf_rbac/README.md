# sf_rbac

Roles, role hierarchy and privilege grants. Runs as `SECURITYADMIN`.
Modules: `snowflake_role`, `snowflake_grant`.

```yaml
sf_roles:                         # parents first
  - name: PLATFORM_ENGINEER
    granted_to_roles: [SYSADMIN]
  - name: PLATFORM_READER
    granted_to_roles: [PLATFORM_ENGINEER]

sf_grants:
  - {privileges: [USAGE], object_type: warehouse, object_name: WH_ANALYTICS_DEV, to_role: PLATFORM_READER}
  - {privileges: [SELECT], object_type: future_views_in_schema, object_name: TASTY_BYTES_DEMO.ANALYTICS, to_role: PLATFORM_READER}
```

`object_type` values: `account, warehouse, database, schema, table, view, all_tables_in_schema, all_views_in_schema,
future_tables_in_schema, future_views_in_schema`. Use explicit privileges (`ALL` is rejected because it cannot be compared).
