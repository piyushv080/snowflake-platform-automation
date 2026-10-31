"""Live Stage 6 check: Cortex Agent diagnosis of the demo incidents.

Writes INCIDENT_DIAGNOSIS + AUDIT_LOGS rows. Reset afterwards with
`python scripts/run_sql.py sql/02_sample_data.sql`.

Usage:  python scripts/check_stage6_agent.py [INC1001 ...]
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.connection import get_session  # noqa: E402
from services.factory import build_snowflake_app  # noqa: E402


def main(incident_ids: list[str]) -> None:
    session = get_session()
    app = build_snowflake_app(session)
    for incident_id in incident_ids:
        started = time.time()
        d = app.diagnosis.diagnose(incident_id, actor="stage6-check", mode="agent")
        print(f"\n=== {incident_id}  ({time.time() - started:.1f}s, mode={d.orchestration_mode}, model={d.ai_model})")
        print("root_cause :", d.root_cause[:220])
        print("confidence :", d.confidence, "| playbook:", d.recommended_playbook, "| policy_valid:", d.policy_valid)
        print("policy     :", d.policy_messages)
        print("tools      :", d.tools_used)
        print("runbooks   :", d.runbooks_used)
    session.close()


if __name__ == "__main__":
    main(sys.argv[1:] or ["INC1001", "INC1002", "INC1003", "INC1004"])
