-- =====================================================================
-- OpsAgentX - Stage 6a: controlled, READ-ONLY agent tools (SQL UDFs)
-- Run as OPSAGENTX_ROLE.
--
-- Security model:
-- * Each tool is a SQL UDF with fixed, typed arguments. The agent can only
--   pass values; it cannot change the SQL that runs. No dynamic SQL.
-- * UDFs cannot modify data (no INSERT/UPDATE/DELETE inside a UDF), so these
--   tools are read-only by construction.
-- * The agent gets NO text-to-SQL tool, NO shell, NO execution tool.
-- * VALIDATE_REMEDIATION here is ADVISORY for the agent. The authoritative
--   check is core/security.py (PolicyEngine), which the application always
--   re-runs before approval and again before execution.
-- =====================================================================
USE ROLE OPSAGENTX_ROLE;
USE WAREHOUSE COMPUTE_WH;
USE SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA;

CREATE OR REPLACE FUNCTION TOOL_GET_INCIDENT_DETAILS(INCIDENT_ID_ARG VARCHAR)
RETURNS OBJECT
COMMENT = 'OpsAgentX agent tool: incident row + server inventory row (read-only)'
AS
$$
    SELECT OBJECT_CONSTRUCT(
        'incident', (SELECT OBJECT_CONSTRUCT_KEEP_NULL(
                         'incident_id', i.INCIDENT_ID, 'source', i.SOURCE, 'server_name', i.SERVER_NAME,
                         'environment', i.ENVIRONMENT, 'severity', i.SEVERITY, 'category', i.CATEGORY,
                         'description', i.DESCRIPTION, 'status', i.STATUS, 'created_at', i.CREATED_AT)
                     FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.INCIDENTS i
                     WHERE i.INCIDENT_ID = UPPER(TRIM(INCIDENT_ID_ARG))),
        'server',   (SELECT OBJECT_CONSTRUCT_KEEP_NULL(
                         'server_name', s.SERVER_NAME, 'environment', s.ENVIRONMENT, 'os', s.OS,
                         'role_description', s.ROLE_DESCRIPTION, 'allow_automation', s.ALLOW_AUTOMATION)
                     FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.SERVERS s
                     JOIN OPSAGENTX_DB.OPSAGENTX_SCHEMA.INCIDENTS i ON i.SERVER_NAME = s.SERVER_NAME
                     WHERE i.INCIDENT_ID = UPPER(TRIM(INCIDENT_ID_ARG)))
    )
$$;

CREATE OR REPLACE FUNCTION TOOL_GET_SERVER_LOGS(SERVER_NAME_ARG VARCHAR)
RETURNS OBJECT
COMMENT = 'OpsAgentX agent tool: latest 30 log lines for one server (read-only)'
AS
$$
    SELECT OBJECT_CONSTRUCT(
        'server_name', LOWER(TRIM(SERVER_NAME_ARG)),
        'logs', COALESCE(ARRAY_AGG(OBJECT_CONSTRUCT(
                    'ts', l.LOG_TIMESTAMP, 'level', l.LOG_LEVEL,
                    'message', l.LOG_MESSAGE, 'incident_id', l.INCIDENT_ID))
                 WITHIN GROUP (ORDER BY l.LOG_TIMESTAMP DESC), ARRAY_CONSTRUCT()))
    FROM (SELECT * FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.INFRASTRUCTURE_LOGS
          WHERE SERVER_NAME = LOWER(TRIM(SERVER_NAME_ARG))
          ORDER BY LOG_TIMESTAMP DESC LIMIT 30) l
$$;

CREATE OR REPLACE FUNCTION TOOL_GET_SERVER_METRICS(SERVER_NAME_ARG VARCHAR)
RETURNS OBJECT
COMMENT = 'OpsAgentX agent tool: metric time series for one server (read-only)'
AS
$$
    SELECT OBJECT_CONSTRUCT(
        'server_name', LOWER(TRIM(SERVER_NAME_ARG)),
        'metrics', COALESCE(ARRAY_AGG(OBJECT_CONSTRUCT(
                       'ts', m.METRIC_TIMESTAMP, 'metric', m.METRIC_NAME,
                       'value', m.METRIC_VALUE, 'mount_point', m.MOUNT_POINT))
                    WITHIN GROUP (ORDER BY m.METRIC_TIMESTAMP), ARRAY_CONSTRUCT()))
    FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.SERVER_METRICS m
    WHERE m.SERVER_NAME = LOWER(TRIM(SERVER_NAME_ARG))
$$;

CREATE OR REPLACE FUNCTION TOOL_GET_HISTORICAL_INCIDENTS(CATEGORY_ARG VARCHAR)
RETURNS OBJECT
COMMENT = 'OpsAgentX agent tool: up to 5 RESOLVED incidents of a category (read-only)'
AS
$$
    SELECT OBJECT_CONSTRUCT(
        'category', LOWER(TRIM(CATEGORY_ARG)),
        'incidents', COALESCE(ARRAY_AGG(OBJECT_CONSTRUCT(
                         'incident_id', h.INCIDENT_ID, 'server_name', h.SERVER_NAME,
                         'description', h.DESCRIPTION)), ARRAY_CONSTRUCT()))
    FROM (SELECT * FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.INCIDENTS
          WHERE CATEGORY = LOWER(TRIM(CATEGORY_ARG)) AND STATUS = 'RESOLVED'
          ORDER BY CREATED_AT DESC LIMIT 5) h
$$;

CREATE OR REPLACE FUNCTION TOOL_ANALYZE_DISK_USAGE(SERVER_NAME_ARG VARCHAR)
RETURNS OBJECT
COMMENT = 'OpsAgentX agent tool: per-mount latest disk %, change, status, /tmp size (read-only)'
AS
$$
    WITH disk AS (
        SELECT COALESCE(MOUNT_POINT, '/') AS MOUNT_POINT, METRIC_VALUE, METRIC_TIMESTAMP
        FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.SERVER_METRICS
        WHERE SERVER_NAME = LOWER(TRIM(SERVER_NAME_ARG)) AND METRIC_NAME = 'disk_used_pct'
    ),
    per_mount AS (
        SELECT MOUNT_POINT,
               MAX_BY(METRIC_VALUE, METRIC_TIMESTAMP) AS LATEST,
               MIN_BY(METRIC_VALUE, METRIC_TIMESTAMP) AS EARLIEST
        FROM disk GROUP BY MOUNT_POINT
    ),
    tmp AS (
        SELECT MAX_BY(METRIC_VALUE, METRIC_TIMESTAMP) AS TMP_GB
        FROM OPSAGENTX_DB.OPSAGENTX_SCHEMA.SERVER_METRICS
        WHERE SERVER_NAME = LOWER(TRIM(SERVER_NAME_ARG)) AND METRIC_NAME = 'tmp_dir_gb'
    )
    SELECT OBJECT_CONSTRUCT_KEEP_NULL(
        'server_name', LOWER(TRIM(SERVER_NAME_ARG)),
        'mounts', (SELECT COALESCE(ARRAY_AGG(OBJECT_CONSTRUCT(
                       'mount_point', MOUNT_POINT, 'latest_used_pct', LATEST,
                       'change_pct_points', ROUND(LATEST - EARLIEST, 1),
                       'status', CASE WHEN LATEST >= 90 THEN 'critical'
                                      WHEN LATEST >= 80 THEN 'warning' ELSE 'ok' END)), ARRAY_CONSTRUCT())
                   FROM per_mount),
        'tmp_dir_gb', (SELECT TMP_GB FROM tmp),
        'likely_cause', IFF((SELECT TMP_GB FROM tmp) > 10, 'temporary directory growth', 'unknown')
    )
$$;

-- Advisory mirror of PolicyEngine rules R1-R6 so the agent can self-check.
CREATE OR REPLACE FUNCTION TOOL_VALIDATE_REMEDIATION(
    PLAYBOOK_NAME_ARG VARCHAR, ENVIRONMENT_ARG VARCHAR, SERVER_NAME_ARG VARCHAR, CATEGORY_ARG VARCHAR)
RETURNS OBJECT
COMMENT = 'OpsAgentX agent tool: ADVISORY policy check of a proposed playbook (read-only). Not authoritative.'
AS
$$
    WITH args AS (
        SELECT TRIM(PLAYBOOK_NAME_ARG) AS PB, LOWER(TRIM(ENVIRONMENT_ARG)) AS ENV,
               LOWER(TRIM(SERVER_NAME_ARG)) AS SRV, LOWER(TRIM(CATEGORY_ARG)) AS CAT
    ),
    checks AS (
        SELECT
            p.PLAYBOOK_NAME IS NOT NULL AND p.ENABLED                       AS R1_ALLOWLISTED,
            p.CATEGORY = a.CAT OR a.CAT IS NULL OR a.CAT = ''              AS R2_CATEGORY_MATCH,
            ARRAY_CONTAINS(a.ENV::VARIANT, p.ALLOWED_ENVIRONMENTS)          AS R3_ENV_ALLOWED,
            s.SERVER_NAME IS NOT NULL                                       AS R4_SERVER_KNOWN,
            COALESCE(s.ALLOW_AUTOMATION, FALSE)                             AS R5_AUTOMATION_ALLOWED,
            s.ENVIRONMENT = a.ENV                                           AS R6_ENV_MATCHES_INVENTORY
        FROM args a
        LEFT JOIN OPSAGENTX_DB.OPSAGENTX_SCHEMA.PLAYBOOKS p ON p.PLAYBOOK_NAME = a.PB
        LEFT JOIN OPSAGENTX_DB.OPSAGENTX_SCHEMA.SERVERS   s ON s.SERVER_NAME   = a.SRV
    )
    SELECT OBJECT_CONSTRUCT(
        'allowed', COALESCE(R1_ALLOWLISTED AND R2_CATEGORY_MATCH AND R3_ENV_ALLOWED
                            AND R4_SERVER_KNOWN AND R5_AUTOMATION_ALLOWED AND R6_ENV_MATCHES_INVENTORY, FALSE),
        'requires_human_approval', TRUE,
        'checks', OBJECT_CONSTRUCT(
            'R1_playbook_allowlisted', COALESCE(R1_ALLOWLISTED, FALSE),
            'R2_category_match', COALESCE(R2_CATEGORY_MATCH, FALSE),
            'R3_environment_allowed', COALESCE(R3_ENV_ALLOWED, FALSE),
            'R4_server_in_inventory', COALESCE(R4_SERVER_KNOWN, FALSE),
            'R5_server_allows_automation', COALESCE(R5_AUTOMATION_ALLOWED, FALSE),
            'R6_environment_matches_inventory', COALESCE(R6_ENV_MATCHES_INVENTORY, FALSE)),
        'note', 'Advisory only. The OpsAgentX policy engine re-validates before approval and execution.')
    FROM checks
$$;
