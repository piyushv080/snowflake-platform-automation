-- =====================================================================
-- OpsAgentX - Stage 2: database, schema, and tables
-- Run as OPSAGENTX_ROLE (created by 00_role_setup.sql).
--
-- Note on constraints: Snowflake ENFORCES NOT NULL but does NOT enforce
-- PRIMARY KEY / FOREIGN KEY / UNIQUE on standard tables. We still declare
-- them (they document intent and help BI tools), and the Python service
-- layer is responsible for real validation.
-- =====================================================================
USE ROLE OPSAGENTX_ROLE;
USE WAREHOUSE COMPUTE_WH;

CREATE DATABASE IF NOT EXISTS OPSAGENTX_DB COMMENT = 'OpsAgentX infrastructure operations platform';
CREATE SCHEMA IF NOT EXISTS OPSAGENTX_DB.OPSAGENTX_SCHEMA;
USE SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA;

-- ---------------------------------------------------------------------
-- Reference / allowlist tables (security layer reads these)
-- ---------------------------------------------------------------------

-- Inventory of servers. ALLOW_AUTOMATION = FALSE means no playbook may target it.
CREATE TABLE IF NOT EXISTS SERVERS (
    SERVER_NAME       VARCHAR(100) NOT NULL PRIMARY KEY,
    ENVIRONMENT       VARCHAR(20)  NOT NULL,      -- development | test | production
    OS                VARCHAR(50),
    ROLE_DESCRIPTION  VARCHAR(200),
    ALLOW_AUTOMATION  BOOLEAN      NOT NULL DEFAULT FALSE
);

-- The ONLY playbooks that may ever be selected. The LLM cannot add to this list.
CREATE TABLE IF NOT EXISTS PLAYBOOKS (
    PLAYBOOK_NAME         VARCHAR(100) NOT NULL PRIMARY KEY,
    CATEGORY              VARCHAR(50)  NOT NULL,
    DESCRIPTION           VARCHAR(500),
    ALLOWED_ENVIRONMENTS  ARRAY        NOT NULL,   -- e.g. ['development','test']
    RISK_LEVEL            VARCHAR(10)  NOT NULL,   -- low | medium | high
    ENABLED               BOOLEAN      NOT NULL DEFAULT TRUE
);

-- ---------------------------------------------------------------------
-- Core operational tables (as specified)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS INCIDENTS (
    INCIDENT_ID   VARCHAR(20)   NOT NULL PRIMARY KEY,
    SOURCE        VARCHAR(50)   NOT NULL,          -- servicenow | github | monitoring | ansible
    SERVER_NAME   VARCHAR(100)  NOT NULL,
    ENVIRONMENT   VARCHAR(20)   NOT NULL,
    SEVERITY      VARCHAR(10)   NOT NULL,          -- low | medium | high | critical
    CATEGORY      VARCHAR(50)   NOT NULL,          -- disk_space | high_cpu | memory | service_failure | ansible_failure | health_check
    DESCRIPTION   VARCHAR(2000) NOT NULL,
    STATUS        VARCHAR(30)   NOT NULL DEFAULT 'OPEN',
    CREATED_AT    TIMESTAMP_LTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT    TIMESTAMP_LTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);
-- STATUS lifecycle: OPEN -> DIAGNOSED -> PENDING_APPROVAL -> APPROVED | REJECTED
--                   -> REMEDIATING -> RESOLVED | REMEDIATION_FAILED

CREATE TABLE IF NOT EXISTS INCIDENT_DIAGNOSIS (
    DIAGNOSIS_ID             VARCHAR(36)   NOT NULL PRIMARY KEY DEFAULT UUID_STRING(),
    INCIDENT_ID              VARCHAR(20)   NOT NULL REFERENCES INCIDENTS(INCIDENT_ID),
    ROOT_CAUSE               VARCHAR(2000),
    CONFIDENCE               NUMBER(4,3),        -- 0.000 - 1.000
    RECOMMENDED_ACTION       VARCHAR(2000),
    RECOMMENDED_PLAYBOOK     VARCHAR(100),
    REQUIRES_HUMAN_APPROVAL  BOOLEAN       NOT NULL DEFAULT TRUE,
    POLICY_VALID             BOOLEAN,            -- result of security validation
    POLICY_MESSAGES          ARRAY,              -- reasons from the policy engine
    TOOLS_USED               ARRAY,              -- diagnostic tools the agent called
    RUNBOOKS_USED            ARRAY,              -- runbook ids retrieved
    AI_MODEL                 VARCHAR(100),
    ORCHESTRATION_MODE       VARCHAR(30),        -- cortex_agent | direct_llm
    RAW_RESPONSE             VARCHAR,            -- raw LLM text, kept for audit/debugging
    CREATED_AT               TIMESTAMP_LTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS INFRASTRUCTURE_LOGS (
    LOG_ID         NUMBER        AUTOINCREMENT START 1 INCREMENT 1 PRIMARY KEY,
    SERVER_NAME    VARCHAR(100)  NOT NULL,
    LOG_LEVEL      VARCHAR(10)   NOT NULL,       -- DEBUG | INFO | WARN | ERROR | CRITICAL
    LOG_MESSAGE    VARCHAR(2000) NOT NULL,
    LOG_TIMESTAMP  TIMESTAMP_LTZ NOT NULL,
    INCIDENT_ID    VARCHAR(20)                    -- nullable: not every log belongs to an incident
);

-- Simulated time-series metrics (disk / cpu / memory) per server.
CREATE TABLE IF NOT EXISTS SERVER_METRICS (
    METRIC_ID      NUMBER        AUTOINCREMENT START 1 INCREMENT 1 PRIMARY KEY,
    SERVER_NAME    VARCHAR(100)  NOT NULL,
    METRIC_NAME    VARCHAR(50)   NOT NULL,       -- disk_used_pct | tmp_dir_gb | cpu_pct | memory_pct | service_up
    METRIC_VALUE   FLOAT         NOT NULL,
    MOUNT_POINT    VARCHAR(100),                  -- for disk metrics
    METRIC_TIMESTAMP TIMESTAMP_LTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS RUNBOOKS (
    RUNBOOK_ID   VARCHAR(20)   NOT NULL PRIMARY KEY,
    TITLE        VARCHAR(200)  NOT NULL,
    CATEGORY     VARCHAR(50)   NOT NULL,
    DESCRIPTION  VARCHAR(1000),
    CONTENT      VARCHAR       NOT NULL,
    APPROVED     BOOLEAN       NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS REMEDIATION_EXECUTIONS (
    EXECUTION_ID         VARCHAR(36)   NOT NULL PRIMARY KEY DEFAULT UUID_STRING(),
    INCIDENT_ID          VARCHAR(20)   NOT NULL REFERENCES INCIDENTS(INCIDENT_ID),
    DIAGNOSIS_ID         VARCHAR(36),
    PLAYBOOK_NAME        VARCHAR(100)  NOT NULL,
    TARGET_SERVER        VARCHAR(100)  NOT NULL,
    APPROVAL_STATUS      VARCHAR(20)   NOT NULL DEFAULT 'PENDING',   -- PENDING | APPROVED | REJECTED
    APPROVED_BY          VARCHAR(100),
    APPROVAL_COMMENT     VARCHAR(1000),
    APPROVED_AT          TIMESTAMP_LTZ,
    EXECUTION_STATUS     VARCHAR(20)   NOT NULL DEFAULT 'NOT_STARTED', -- NOT_STARTED | RUNNING | SUCCEEDED | FAILED | TIMED_OUT | VERIFICATION_FAILED | CANCELLED
    EXECUTION_OUTPUT     VARCHAR,
    VERIFICATION_RESULT  VARIANT,
    CREATED_AT           TIMESTAMP_LTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    COMPLETED_AT         TIMESTAMP_LTZ
);

-- Append-only audit trail. The app never UPDATEs or DELETEs rows here.
CREATE TABLE IF NOT EXISTS AUDIT_LOGS (
    AUDIT_ID     VARCHAR(36)   NOT NULL PRIMARY KEY DEFAULT UUID_STRING(),
    INCIDENT_ID  VARCHAR(20),
    ACTION       VARCHAR(50)   NOT NULL,     -- INCIDENT_CREATED | DIAGNOSIS_REQUESTED | ... 
    ACTOR        VARCHAR(100)  NOT NULL,     -- user name, 'opsagentx-agent', 'policy-engine', 'ansible-sim'
    DETAILS      VARIANT,
    CREATED_AT   TIMESTAMP_LTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);
