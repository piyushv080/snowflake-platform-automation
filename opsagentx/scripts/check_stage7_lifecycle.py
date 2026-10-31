"""Live Stage 7 check: full incident lifecycles against Snowflake (simulated Ansible).

Scenarios
  INC1001  agent diagnosis -> request -> approve -> execute (success) -> RESOLVED
  INC1003  direct diagnosis -> request -> REJECT -> execution blocked
  INC1002  direct diagnosis -> request -> approve -> execute (simulated FAILURE)
  INC1004  direct diagnosis -> request refused by policy (prod-db-01)

Writes rows; reset with `python scripts/run_sql.py sql/02_sample_data.sql`.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.exceptions import ApprovalRequiredError, PolicyViolationError  # noqa: E402
from database.connection import get_session  # noqa: E402
from integrations.ansible import SimulatedAnsibleClient, SimulationScenario  # noqa: E402
from services.factory import build_snowflake_app  # noqa: E402

HUMAN = os.environ.get("SNOWFLAKE_USERNAME", "demo-operator")


def show(app, incident_id):
    incident = app.incidents.get(incident_id)
    print(f"   -> incident {incident_id} status: {incident.status.value}")


def main() -> None:
    session = get_session()
    app = build_snowflake_app(session)

    print("\n### INC1001 - main demo (agent, approve, success)")
    d = app.diagnosis.diagnose("INC1001", actor=HUMAN, mode="agent")
    print(f"   diagnosis: {d.recommended_playbook} conf={d.confidence} policy_valid={d.policy_valid} tools={len(d.tools_used)}")
    ex = app.approvals.request_approval("INC1001", requested_by=HUMAN)
    print(f"   approval requested: {ex.execution_id} {ex.playbook_name} -> {ex.target_server} [{ex.approval_status.value}]")
    show(app, "INC1001")
    ex = app.approvals.approve(ex.execution_id, approver=HUMAN, comment="dev server, low risk", confirmed=True)
    print(f"   approved by {ex.approved_by}")
    ex = app.remediation.execute(ex.execution_id, actor=HUMAN)
    print(f"   execution: {ex.execution_status.value}; verification: {ex.verification_result}")
    print("   output tail:", ex.execution_output.splitlines()[-1])
    show(app, "INC1001")

    print("\n### INC1003 - rejection")
    app.diagnosis.diagnose("INC1003", actor=HUMAN, mode="direct")
    ex = app.approvals.request_approval("INC1003", requested_by=HUMAN)
    ex = app.approvals.reject(ex.execution_id, approver=HUMAN, reason="check nginx config manually first")
    print(f"   rejected: approval={ex.approval_status.value} execution={ex.execution_status.value}")
    try:
        app.remediation.execute(ex.execution_id, actor=HUMAN)
    except ApprovalRequiredError as error:
        print("   execute attempt blocked:", error)
    show(app, "INC1003")

    print("\n### INC1002 - simulated Ansible failure")
    app.diagnosis.diagnose("INC1002", actor=HUMAN, mode="direct")
    ex = app.approvals.request_approval("INC1002", requested_by=HUMAN)
    ex = app.approvals.approve(ex.execution_id, approver=HUMAN, confirmed=True)
    ex = app.remediation.execute(ex.execution_id, actor=HUMAN,
                                 ansible=SimulatedAnsibleClient(SimulationScenario.FAILURE))
    print(f"   execution: {ex.execution_status.value}; verification: {ex.verification_result}")
    show(app, "INC1002")

    print("\n### INC1004 - policy refuses approval request")
    app.diagnosis.diagnose("INC1004", actor=HUMAN, mode="direct")
    try:
        app.approvals.request_approval("INC1004", requested_by=HUMAN)
    except PolicyViolationError as error:
        print("   request refused:", error)
    show(app, "INC1004")
    session.close()


if __name__ == "__main__":
    main()
