-- =====================================================================
-- OpsAgentX - Stage 5: Cortex Search service over approved runbooks
-- Run as OPSAGENTX_ROLE.
--
-- Design notes:
-- * Only APPROVED runbooks are indexed. Draft RB-900 never enters the index,
--   so neither the app nor the agent can retrieve it (defense in depth on top
--   of the application-level filter).
-- * SEARCH_TEXT concatenates title + description + content so semantic search
--   sees the full context; CONTENT is returned separately for display.
-- * CATEGORY is an ATTRIBUTE so callers can filter, e.g. {"@eq": {"CATEGORY": "disk_space"}}.
-- * TARGET_LAG = 1 hour is plenty for a slowly changing runbook library.
-- =====================================================================
USE ROLE OPSAGENTX_ROLE;
USE WAREHOUSE COMPUTE_WH;
USE SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA;

-- Cortex Search refreshes incrementally from change tracking on the source table.
ALTER TABLE RUNBOOKS SET CHANGE_TRACKING = TRUE;

CREATE OR REPLACE CORTEX SEARCH SERVICE RUNBOOK_SEARCH
  ON SEARCH_TEXT
  ATTRIBUTES CATEGORY
  WAREHOUSE = COMPUTE_WH
  TARGET_LAG = '1 hour'
  COMMENT = 'OpsAgentX: semantic search over APPROVED infrastructure runbooks'
AS
  SELECT
      RUNBOOK_ID,
      TITLE,
      CATEGORY,
      DESCRIPTION,
      CONTENT,
      TITLE || '\n' || COALESCE(DESCRIPTION, '') || '\n' || CONTENT AS SEARCH_TEXT
  FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.RUNBOOKS
  WHERE APPROVED = TRUE;
