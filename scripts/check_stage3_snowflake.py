"""Integration check: run the repository + incident service against real Snowflake.

Not part of the default pytest run (it needs a live session and writes rows).
It ingests a throwaway incident INC9001, exercises the lifecycle, prints
results, and the caller resets the demo data afterwards with
`python scripts/run_sql.py sql/02_sample_data.sql`.

Usage:  python scripts/check_stage3_snowflake.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.exceptions import IncidentValidationError, InvalidStateTransitionError  # noqa: E402
from core.models import IncidentFilter, IncidentStatus  # noqa: E402
from database.connection import get_session  # noqa: E402
from database.snowflake_repository import SnowflakeRepository  # noqa: E402
from services.audit_service import AuditService  # noqa: E402
from services.incident_service import IncidentService  # noqa: E402


def main() -> None:
    session = get_session()
    repo = SnowflakeRepository(session)
    audit = AuditService(repo)
    incidents = IncidentService(repo, audit)

    inc = incidents.get("INC1001")
    print("READ incident     :", inc.incident_id, inc.server_name, inc.environment.value, inc.severity.value, inc.status.value)
    print("READ logs         :", [l.log_message[:40] for l in repo.get_logs("server-01", 3)])
    print("READ metrics      :", [(m.metric_name, m.metric_value) for m in repo.get_metrics("server-01")][-3:])
    print("READ history      :", [i.incident_id for i in repo.get_historical_incidents("disk_space")])
    print("READ server       :", repo.get_server("prod-db-01"))
    print("READ playbooks    :", [(p.playbook_name, [e.value for e in p.allowed_environments]) for p in repo.list_playbooks()][:2])
    print("READ runbooks     :", [r.runbook_id for r in repo.list_runbooks()], "(approved only)")
    print("FILTER open/high  :", [i.incident_id for i in incidents.list(IncidentFilter(status="OPEN", severity="high"))])

    created = incidents.ingest({
        "incident_id": "INC9001", "source": "gh", "server_name": "Server-02", "environment": "qa",
        "severity": "sev2", "category": "ansible", "description": "Integration test incident - safe to delete.",
    }, actor="stage3-check")
    print("WRITE ingest      :", created.incident_id, created.source.value, created.environment.value,
          created.severity.value, created.category.value, created.status.value)

    try:
        incidents.ingest({"incident_id": "BAD", "source": "x"}, actor="stage3-check")
    except IncidentValidationError as error:
        print("WRITE invalid     : rejected ->", str(error)[:90], "...")

    incidents.change_status("INC9001", IncidentStatus.DIAGNOSED, actor="stage3-check", reason="test")
    try:
        incidents.change_status("INC9001", IncidentStatus.RESOLVED, actor="stage3-check")
    except InvalidStateTransitionError as error:
        print("LIFECYCLE guard   :", error)

    print("AUDIT timeline    :", [(e.action.value, e.actor) for e in audit.timeline("INC9001")])
    session.close()


if __name__ == "__main__":
    main()
