"""Live Stage 4 check: real Cortex AI diagnosis of the demo incidents in Snowflake.

Writes INCIDENT_DIAGNOSIS + AUDIT_LOGS rows. Reset afterwards with
`python scripts/run_sql.py sql/02_sample_data.sql` if you want a clean demo.

Usage:  python scripts/check_stage4_diagnosis.py [INC1001 INC1002 ...]
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
    service = app.diagnosis
    for incident_id in incident_ids:
        started = time.time()
        d = service.diagnose(incident_id, actor="stage4-check")
        print(f"\n=== {incident_id}  ({time.time() - started:.1f}s, model={d.ai_model})")
        print("root_cause   :", d.root_cause)
        print("confidence   :", d.confidence)
        print("action       :", d.recommended_action)
        print("playbook     :", d.recommended_playbook, "| policy_valid:", d.policy_valid)
        print("policy       :", d.policy_messages)
        print("tools        :", d.tools_used)
        print("runbooks     :", d.runbooks_used, "| retriever:", app.retriever.name,
              "via", app.retriever.primary.last_api)
    session.close()


if __name__ == "__main__":
    main(sys.argv[1:] or ["INC1001", "INC1002", "INC1003", "INC1004"])
