# sf_demo_data

Generates **Tasty Bytes style** demo data inside Snowflake with `GENERATOR` - no external files or stages.
The database and schemas come from `sf_database` (`TASTY_BYTES_DEMO.RAW_POS / RAW_CUSTOMER / ANALYTICS`).

Tables: `COUNTRY`, `MENU`, `TRUCK`, `ORDER_HEADER`, `ORDER_DETAIL`, `CUSTOMER_LOYALTY`.
Views: `ANALYTICS.ORDERS_V`, `ANALYTICS.DAILY_SALES_V`.

Skips the load when `ORDER_DETAIL` already exists; `-e sf_demo_reload=true` rebuilds.

| Variable | Default |
|----------|---------|
| `sf_demo_trucks` | 50 |
| `sf_demo_customers` | 5000 |
| `sf_demo_orders` | 20000 |
| `sf_demo_lines_per_order` | 2 |
