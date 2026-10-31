# Snowflake Platform Automation

This repository contains two related projects with separate code, dependencies, and deployment workflows:

| Project | Purpose | Documentation |
|---|---|---|
| OpsAgentX | Streamlit and Snowflake Cortex incident diagnosis with human-approved, simulated remediation | [opsagentx/README.md](opsagentx/README.md) |
| Snowflake IAC | Ansible/AWX automation for provisioning and operating Snowflake | [snowflake-iac/README.md](snowflake-iac/README.md) |

## Development

Work on a focused branch from `dev`, for example `feature/opsagentx-ui` or `feature/iac-monitoring`, and merge it back into `dev` through a pull request. Keep `main` for reviewed, stable releases. Changes to one project should normally stay within its directory; the IAC GitHub Actions workflow runs only when `snowflake-iac/` changes.

Run each project from its own directory and follow its README for setup and deployment instructions.
