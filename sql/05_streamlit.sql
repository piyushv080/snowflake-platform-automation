-- =====================================================================
-- OpsAgentX - Stage 8: Streamlit in Snowflake app
-- Run as OPSAGENTX_ROLE AFTER uploading the app files to the stage
-- (scripts/deploy_streamlit.py does both steps).
--
-- Runtime choice: WAREHOUSE runtime (SYSTEM$WAREHOUSE_RUNTIME).
--   * Dependencies come from the Snowflake Anaconda channel via environment.yml
--     (pydantic 2.8.2, streamlit 1.52.2) - no PyPI, no external access integration.
--   * The Cortex Agent is called through SQL (SNOWFLAKE.CORTEX.DATA_AGENT_RUN),
--     so the container runtime is not required for agent access.
--   * The container runtime would need pydantic from a package index
--     (Snowflake artifact repository or EAI), which is not configured here.
-- =====================================================================
USE ROLE OPSAGENTX_ROLE;
USE WAREHOUSE COMPUTE_WH;
USE SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA;

CREATE STAGE IF NOT EXISTS APP_STAGE
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'OpsAgentX Streamlit app source files';

CREATE OR REPLACE STREAMLIT OPSAGENTX_APP
  FROM '@OPSAGENTX_DB.OPSAGENTX_SCHEMA.APP_STAGE/opsagentx'
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = COMPUTE_WH
  RUNTIME_NAME = 'SYSTEM$WAREHOUSE_RUNTIME'
  TITLE = 'OpsAgentX'
  COMMENT = 'OpsAgentX: AI-assisted infrastructure incident diagnosis, approval and simulated remediation';
