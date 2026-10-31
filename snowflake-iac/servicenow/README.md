# ServiceNow integration

ServiceNow is the **front door**; AWX does the work; Ansible writes the result back.

1. Catalog item *Request Snowflake warehouse* (variables: name `^WH_[A-Z0-9_]{3,40}$`, size, quota, environment,
   justification) creates a Normal change with `u_platform = snowflake` and `u_snowflake_action = provision`.
2. An engineer opens a pull request adding the warehouse to `group_vars/env_<env>/platform.yml`, linked to the CR.
3. When the CR reaches **Implement**, `business_rule_launch_awx.js` launches the AWX job template with `cr_number`.
4. The playbook (`servicenow_change/gate_start`) re-checks the CR state, runs, and posts work notes (`gate_end`).
5. Failures and self-healing actions create incidents (`servicenow_change/incident`).

## System properties

| Property | Example |
|----------|---------|
| `x_platform.awx.url` | `https://awx.example.com` |
| `x_platform.awx.token` (password2) | AWX OAuth2 token |
| `x_platform.awx.template.provision` | `12` (id of *SF - 02 Provision Platform (prod)*) |
| `x_platform.awx.template.user_lifecycle` | `14` |

The AWX job template must allow the survey/extra var `cr_number` (survey or *Prompt on launch* for variables).
For a local laptop AWX, ServiceNow cloud cannot reach it unless you use a MID Server or a tunnel -
you can still demo the gate by launching the template manually with a real CR number.
