-- =====================================================================
-- OpsAgentX - Stage 1: least-privilege role setup
-- Run ONCE as ACCOUNTADMIN (or SECURITYADMIN + SYSADMIN split).
-- After this, every other script runs as OPSAGENTX_ROLE.
-- =====================================================================
USE ROLE ACCOUNTADMIN;

-- Dedicated application role. Nothing in the app should run as ACCOUNTADMIN.
CREATE ROLE IF NOT EXISTS OPSAGENTX_ROLE
  COMMENT = 'OpsAgentX application owner role (least privilege)';

-- Keep the role inside the standard hierarchy so SYSADMIN can manage its objects.
GRANT ROLE OPSAGENTX_ROLE TO ROLE SYSADMIN;

-- Lets the role create (and therefore own) OPSAGENTX_DB.
GRANT CREATE DATABASE ON ACCOUNT TO ROLE OPSAGENTX_ROLE;

-- Compute for SQL, UDFs, procedures, and the Streamlit query warehouse.
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE OPSAGENTX_ROLE;

-- Compute pool for the Streamlit container runtime (needed for Cortex Agents REST calls).
GRANT USAGE ON COMPUTE POOL SYSTEM_COMPUTE_POOL_CPU TO ROLE OPSAGENTX_ROLE;

-- Cortex AI functions, Cortex Search, and Cortex Agents.
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE OPSAGENTX_ROLE;

-- Grant to the developer. Replace the user name when deploying elsewhere.
GRANT ROLE OPSAGENTX_ROLE TO USER PIYUSHVERMA701;
