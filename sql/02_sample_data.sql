-- =====================================================================
-- OpsAgentX - Stage 2: synthetic sample data (as a reusable procedure)
-- Run as OPSAGENTX_ROLE. Safe to re-run: RESET_DEMO_DATA() TRUNCATEs the demo
-- tables first. The Streamlit app's "Reset demo" button calls the same procedure,
-- so there is exactly one definition of the demo data.
-- All data is fictional. No real servers are referenced.
-- =====================================================================
USE ROLE OPSAGENTX_ROLE;
USE WAREHOUSE COMPUTE_WH;
USE SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA;

CREATE OR REPLACE PROCEDURE RESET_DEMO_DATA()
RETURNS VARCHAR
LANGUAGE SQL
COMMENT = 'OpsAgentX: reset all demo tables to the synthetic sample data'
EXECUTE AS OWNER
AS
$$
BEGIN
TRUNCATE TABLE IF EXISTS AUDIT_LOGS;
TRUNCATE TABLE IF EXISTS REMEDIATION_EXECUTIONS;
TRUNCATE TABLE IF EXISTS INCIDENT_DIAGNOSIS;
TRUNCATE TABLE IF EXISTS INFRASTRUCTURE_LOGS;
TRUNCATE TABLE IF EXISTS SERVER_METRICS;
TRUNCATE TABLE IF EXISTS INCIDENTS;
TRUNCATE TABLE IF EXISTS RUNBOOKS;
TRUNCATE TABLE IF EXISTS PLAYBOOKS;
TRUNCATE TABLE IF EXISTS SERVERS;

-- ---------------------------------------------------------------------
-- Servers (approved-target allowlist)
-- prod-db-01 is deliberately NOT automation-enabled to demo a policy block.
-- ---------------------------------------------------------------------
INSERT INTO SERVERS (SERVER_NAME, ENVIRONMENT, OS, ROLE_DESCRIPTION, ALLOW_AUTOMATION) VALUES
  ('server-01',  'development', 'RHEL 9',       'Dev application server',      TRUE),
  ('server-02',  'test',        'RHEL 9',       'Test batch-processing server', TRUE),
  ('server-03',  'development', 'Ubuntu 22.04', 'Dev web server (nginx)',       TRUE),
  ('prod-app-01','production',  'RHEL 9',       'Production app server',        TRUE),
  ('prod-db-01', 'production',  'RHEL 9',       'Production database server',   FALSE);

-- ---------------------------------------------------------------------
-- Playbook allowlist. Anything not in this table is rejected by policy.
-- ---------------------------------------------------------------------
INSERT INTO PLAYBOOKS (PLAYBOOK_NAME, CATEGORY, DESCRIPTION, ALLOWED_ENVIRONMENTS, RISK_LEVEL, ENABLED)
SELECT 'disk_cleanup.yml', 'disk_space', 'Remove files older than 7 days from approved temp/log paths only (/tmp, /var/tmp, /var/log/app/archive).', ARRAY_CONSTRUCT('development','test','production'), 'low', TRUE
UNION ALL SELECT 'restart_service.yml', 'service_failure', 'Restart a named systemd service from the approved service list and confirm it is active.', ARRAY_CONSTRUCT('development','test','production'), 'medium', TRUE
UNION ALL SELECT 'cpu_diagnostics.yml', 'high_cpu', 'Collect top/ps/pidstat snapshots. Read-only; makes no changes.', ARRAY_CONSTRUCT('development','test','production'), 'low', TRUE
UNION ALL SELECT 'restart_high_cpu_process.yml', 'high_cpu', 'Gracefully restart an approved application process that is consuming excessive CPU.', ARRAY_CONSTRUCT('development','test'), 'medium', TRUE
UNION ALL SELECT 'memory_diagnostics.yml', 'memory', 'Collect memory usage snapshots. Read-only; makes no changes.', ARRAY_CONSTRUCT('development','test','production'), 'low', TRUE
UNION ALL SELECT 'ansible_rerun_job.yml', 'ansible_failure', 'Re-run a failed AAP job template after prerequisites are fixed.', ARRAY_CONSTRUCT('development','test'), 'medium', TRUE;

-- ---------------------------------------------------------------------
-- Runbooks (knowledge base for Cortex Search)
-- ---------------------------------------------------------------------
INSERT INTO RUNBOOKS (RUNBOOK_ID, TITLE, CATEGORY, DESCRIPTION, CONTENT, APPROVED) VALUES
('RB-001', 'Linux Disk Space Troubleshooting', 'disk_space',
 'Investigate and remediate high filesystem utilization on Linux servers.',
'Symptoms: filesystem usage above 85% (warning) or 90% (critical); alerts such as "Disk usage crossed 90%"; application write failures.
Investigation steps:
1. Run df -h to identify the full mount point.
2. Run du -xh --max-depth=1 / | sort -h to find large directories.
3. Check /tmp and /var/tmp growth; temporary directory growth is the most common cause on application servers.
4. Check /var/log for unrotated logs and verify logrotate configuration.
5. Confirm the application service is still running before any cleanup.
Common root causes: temporary files not cleaned by the application, log rotation failure, core dumps, large uploads.
Approved remediation: run playbook disk_cleanup.yml, which removes files older than 7 days from /tmp, /var/tmp and /var/log/app/archive only. Never delete files outside these paths.
Verification: disk usage on the affected mount must drop below 80%.
Escalation: if usage remains above 90% after cleanup, escalate to the storage team for volume expansion.', TRUE),
('RB-002', 'High CPU Utilization Investigation', 'high_cpu',
 'Diagnose sustained CPU utilization above 90%.',
'Symptoms: CPU utilization above 90% for more than 10 minutes; increased response latency; load average above core count.
Investigation steps:
1. Run top -o %CPU and ps aux --sort=-%cpu to identify the top process.
2. Use pidstat 5 3 to confirm sustained usage.
3. Check for runaway batch jobs, infinite loops after a recent deployment, or missing cron locks.
4. Correlate with recent deployments in GitHub Actions or Ansible.
Approved remediation: first run cpu_diagnostics.yml (read-only). If an approved application process is identified as runaway in a non-production environment, run restart_high_cpu_process.yml.
Production: never restart processes automatically; open a change request.
Verification: CPU utilization must fall below 70% within 10 minutes.', TRUE),
('RB-003', 'Memory Utilization Troubleshooting', 'memory',
 'Investigate high memory usage and OOM events.',
'Symptoms: memory usage above 90%; OOM killer messages in dmesg or journal; swap usage growing.
Investigation steps:
1. Run free -m and vmstat 5.
2. Identify top consumers with ps aux --sort=-%mem.
3. Check journalctl -k for "Out of memory: Killed process".
4. Look for memory leaks after recent releases.
Approved remediation: run memory_diagnostics.yml (read-only) and escalate to the application owner. Restarts require a separate approved request.
Verification: memory usage below 80% and no new OOM events for 30 minutes.', TRUE),
('RB-004', 'Linux Service Failure Investigation', 'service_failure',
 'Investigate a systemd service that stopped unexpectedly.',
'Symptoms: health check fails; systemctl status shows inactive (dead) or failed; "service stopped unexpectedly" in logs.
Investigation steps:
1. Run systemctl status <service> and journalctl -u <service> -n 200.
2. Check for configuration errors, port conflicts, missing dependencies, or OOM kills.
3. Verify the service binary and configuration were not changed by a recent deployment.
Approved remediation: run restart_service.yml for services on the approved list (nginx, app-api, worker). The playbook restarts the service and confirms it is active.
Verification: systemctl is-active returns active and the health check endpoint returns HTTP 200.
If the service fails again within 15 minutes, do not restart again; escalate to the service owner.', TRUE),
('RB-005', 'Ansible Execution Failure Troubleshooting', 'ansible_failure',
 'Investigate failed Ansible Automation Platform job runs.',
'Symptoms: AAP job status failed; tasks show UNREACHABLE or FAILED; deployment pipeline blocked.
Investigation steps:
1. Review the job stdout in AAP for the first failed task.
2. UNREACHABLE: check SSH connectivity, credentials and inventory host names.
3. FAILED: check module arguments, missing packages, and privilege escalation (become).
4. Check whether the target host changed recently.
Approved remediation: after fixing the prerequisite, run ansible_rerun_job.yml in development or test. Production re-runs require change approval.
Verification: the job finishes with status successful and zero failed hosts.', TRUE),
('RB-900', 'DRAFT - Aggressive disk purge', 'disk_space',
 'Unreviewed draft. Must not be used for recommendations.',
'DRAFT ONLY. Proposes rm -rf on application data directories. Not approved by change advisory board.', FALSE);

-- ---------------------------------------------------------------------
-- Incidents: 3 open demo incidents + historical resolved ones
-- ---------------------------------------------------------------------
INSERT INTO INCIDENTS (INCIDENT_ID, SOURCE, SERVER_NAME, ENVIRONMENT, SEVERITY, CATEGORY, DESCRIPTION, STATUS, CREATED_AT, UPDATED_AT) VALUES
-- Main demo scenario
('INC1001', 'monitoring', 'server-01', 'development', 'high', 'disk_space',
 'Disk utilization reached 95% on / of server-01.', 'OPEN',
 DATEADD('minute', -30, CURRENT_TIMESTAMP()), DATEADD('minute', -30, CURRENT_TIMESTAMP())),
-- Additional demo: high CPU
('INC1002', 'monitoring', 'server-02', 'test', 'high', 'high_cpu',
 'CPU utilization above 90% for 25 minutes on server-02 after nightly batch deploy.', 'OPEN',
 DATEADD('minute', -45, CURRENT_TIMESTAMP()), DATEADD('minute', -45, CURRENT_TIMESTAMP())),
-- Additional demo: stopped Linux service
('INC1003', 'servicenow', 'server-03', 'development', 'critical', 'service_failure',
 'nginx service stopped unexpectedly on server-03; health check failing.', 'OPEN',
 DATEADD('minute', -15, CURRENT_TIMESTAMP()), DATEADD('minute', -15, CURRENT_TIMESTAMP())),
-- Policy demo: production DB server that is NOT automation-enabled
('INC1004', 'monitoring', 'prod-db-01', 'production', 'critical', 'disk_space',
 'Disk utilization reached 93% on /var on prod-db-01.', 'OPEN',
 DATEADD('minute', -10, CURRENT_TIMESTAMP()), DATEADD('minute', -10, CURRENT_TIMESTAMP())),
-- Historical (resolved) incidents for get_historical_incidents
('INC0901', 'monitoring', 'server-01', 'development', 'medium', 'disk_space',
 'Disk utilization reached 88% on /; root cause was /tmp growth from report exports.', 'RESOLVED',
 DATEADD('day', -20, CURRENT_TIMESTAMP()), DATEADD('day', -20, CURRENT_TIMESTAMP())),
('INC0902', 'monitoring', 'server-02', 'test', 'high', 'high_cpu',
 'CPU at 97% caused by a batch job stuck in a retry loop; resolved by restarting the batch worker.', 'RESOLVED',
 DATEADD('day', -12, CURRENT_TIMESTAMP()), DATEADD('day', -12, CURRENT_TIMESTAMP())),
('INC0903', 'servicenow', 'server-03', 'development', 'high', 'service_failure',
 'nginx stopped after config reload with a typo; fixed config and restarted service.', 'RESOLVED',
 DATEADD('day', -7, CURRENT_TIMESTAMP()), DATEADD('day', -7, CURRENT_TIMESTAMP())),
('INC0904', 'ansible', 'server-02', 'test', 'medium', 'ansible_failure',
 'AAP job failed with UNREACHABLE due to rotated SSH key; re-ran after updating credential.', 'RESOLVED',
 DATEADD('day', -5, CURRENT_TIMESTAMP()), DATEADD('day', -5, CURRENT_TIMESTAMP()));

-- ---------------------------------------------------------------------
-- Logs
-- ---------------------------------------------------------------------
INSERT INTO INFRASTRUCTURE_LOGS (SERVER_NAME, LOG_LEVEL, LOG_MESSAGE, LOG_TIMESTAMP, INCIDENT_ID) VALUES
-- INC1001 (the three specified demo log lines + context)
('server-01', 'INFO',     'Disk usage on / at 78%.',                                         DATEADD('minute', -120, CURRENT_TIMESTAMP()), NULL),
('server-01', 'WARN',     'Disk usage crossed 90% on / (91.2%).',                            DATEADD('minute', -40,  CURRENT_TIMESTAMP()), 'INC1001'),
('server-01', 'WARN',     'Temporary directory usage increased: /tmp grew from 2.1 GB to 18.4 GB in 6 hours.', DATEADD('minute', -35, CURRENT_TIMESTAMP()), 'INC1001'),
('server-01', 'INFO',     'Application service app-api is still running (active).',          DATEADD('minute', -32,  CURRENT_TIMESTAMP()), 'INC1001'),
('server-01', 'CRITICAL', 'Disk usage on / at 95%.',                                         DATEADD('minute', -30,  CURRENT_TIMESTAMP()), 'INC1001'),
-- INC1002
('server-02', 'INFO',     'Deployment of batch-worker v2.4.0 completed via GitHub Actions.', DATEADD('minute', -70,  CURRENT_TIMESTAMP()), NULL),
('server-02', 'WARN',     'CPU utilization 92% (load average 7.8 on 4 cores).',              DATEADD('minute', -50,  CURRENT_TIMESTAMP()), 'INC1002'),
('server-02', 'WARN',     'Process batch-worker (pid 4412) using 340% CPU.',                 DATEADD('minute', -47,  CURRENT_TIMESTAMP()), 'INC1002'),
('server-02', 'ERROR',    'batch-worker: retrying job 8812 (attempt 1500) - upstream returned 503.', DATEADD('minute', -46, CURRENT_TIMESTAMP()), 'INC1002'),
-- INC1003
('server-03', 'INFO',     'nginx configuration reloaded by deploy user.',                    DATEADD('minute', -20,  CURRENT_TIMESTAMP()), NULL),
('server-03', 'ERROR',    'nginx.service: Main process exited, code=exited, status=1/FAILURE.', DATEADD('minute', -16, CURRENT_TIMESTAMP()), 'INC1003'),
('server-03', 'ERROR',    'nginx.service: Failed with result exit-code. Service stopped unexpectedly.', DATEADD('minute', -16, CURRENT_TIMESTAMP()), 'INC1003'),
('server-03', 'CRITICAL', 'Health check https://server-03/health failed: connection refused.', DATEADD('minute', -15, CURRENT_TIMESTAMP()), 'INC1003'),
-- INC1004
('prod-db-01','WARN',     'Disk usage crossed 90% on /var (90.4%).',                         DATEADD('minute', -14,  CURRENT_TIMESTAMP()), 'INC1004'),
('prod-db-01','CRITICAL', 'Disk usage on /var at 93%; database WAL directory growing.',      DATEADD('minute', -10,  CURRENT_TIMESTAMP()), 'INC1004');

-- ---------------------------------------------------------------------
-- Metrics (simulated current state used by diagnostic tools)
-- ---------------------------------------------------------------------
INSERT INTO SERVER_METRICS (SERVER_NAME, METRIC_NAME, METRIC_VALUE, MOUNT_POINT, METRIC_TIMESTAMP) VALUES
('server-01', 'disk_used_pct', 78.0, '/',    DATEADD('minute', -120, CURRENT_TIMESTAMP())),
('server-01', 'disk_used_pct', 91.2, '/',    DATEADD('minute', -40,  CURRENT_TIMESTAMP())),
('server-01', 'disk_used_pct', 95.0, '/',    DATEADD('minute', -30,  CURRENT_TIMESTAMP())),
('server-01', 'tmp_dir_gb',    18.4, '/tmp', DATEADD('minute', -30,  CURRENT_TIMESTAMP())),
('server-01', 'cpu_pct',       22.0, NULL,   DATEADD('minute', -30,  CURRENT_TIMESTAMP())),
('server-01', 'memory_pct',    48.0, NULL,   DATEADD('minute', -30,  CURRENT_TIMESTAMP())),
('server-01', 'service_up',     1.0, 'app-api', DATEADD('minute', -30, CURRENT_TIMESTAMP())),
('server-02', 'cpu_pct',       96.5, NULL,   DATEADD('minute', -45,  CURRENT_TIMESTAMP())),
('server-02', 'memory_pct',    61.0, NULL,   DATEADD('minute', -45,  CURRENT_TIMESTAMP())),
('server-02', 'disk_used_pct', 54.0, '/',    DATEADD('minute', -45,  CURRENT_TIMESTAMP())),
('server-03', 'service_up',     0.0, 'nginx', DATEADD('minute', -15, CURRENT_TIMESTAMP())),
('server-03', 'cpu_pct',        3.0, NULL,   DATEADD('minute', -15,  CURRENT_TIMESTAMP())),
('server-03', 'disk_used_pct', 41.0, '/',    DATEADD('minute', -15,  CURRENT_TIMESTAMP())),
('prod-db-01','disk_used_pct', 93.0, '/var', DATEADD('minute', -10,  CURRENT_TIMESTAMP()));

-- Seed audit rows so the timeline shows ingestion of the demo incidents.
INSERT INTO AUDIT_LOGS (INCIDENT_ID, ACTION, ACTOR, DETAILS)
SELECT INCIDENT_ID, 'INCIDENT_CREATED', 'sample-data-loader',
       OBJECT_CONSTRUCT('source', SOURCE, 'severity', SEVERITY, 'server', SERVER_NAME)
FROM INCIDENTS WHERE STATUS = 'OPEN';

    RETURN 'OpsAgentX demo data reset';
END;
$$;

CALL RESET_DEMO_DATA();
