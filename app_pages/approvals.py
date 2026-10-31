"""Approval center: explicit human approve / reject, plus approval history."""
import streamlit as st

from core.exceptions import OpsAgentXError
from ui.common import current_user, executions_df, get_app, refresh, show_error

app = get_app()
user = current_user()
st.caption(f"Signed in as **{user}**. Your Snowflake user name is recorded as the approver.")

pending = [e for e in app.repository.list_executions() if e.approval_status.value == "PENDING"]
if not pending:
    st.info("No remediation is waiting for approval. Request one from **AI Diagnosis**.",
            icon=":material/inbox:")

for execution in pending:
    incident = app.incidents.get(execution.incident_id)
    diagnosis = app.repository.get_latest_diagnosis(execution.incident_id)
    decision = app.policy.validate_remediation(
        playbook_name=execution.playbook_name, server_name=execution.target_server,
        environment=incident.environment.value, category=incident.category.value,
    )
    with st.container(border=True):
        st.subheader(f"{incident.incident_id}: run `{execution.playbook_name}` on `{execution.target_server}`")
        st.markdown(f":gray-badge[{incident.environment.value}] :gray-badge[{incident.severity.value}] "
                    f":gray-badge[{incident.category.value}]  \n{incident.description}")
        if diagnosis:
            st.markdown(f"**AI root cause** ({diagnosis.confidence:.0%} confidence, "
                        f"{diagnosis.orchestration_mode}): {diagnosis.root_cause}")
            st.markdown(f"**Proposed action:** {diagnosis.recommended_action}")
        playbook = next((p for p in app.repository.list_playbooks()
                         if p.playbook_name == execution.playbook_name), None)
        if playbook:
            st.markdown(f"**Playbook does:** {playbook.description} · risk **{playbook.risk_level}**")
        if decision.allowed:
            st.success("Policy re-check passed just now.", icon=":material/verified_user:")
        else:
            st.error("Policy re-check FAILED - approval will be refused: " + "; ".join(decision.messages))
        if incident.environment.value == "production":
            st.warning("Production target: confirm a change record exists before approving.",
                       icon=":material/warning:")

        key = execution.execution_id
        confirmed = st.checkbox(
            f"I have reviewed the evidence and authorize the **simulated** run of "
            f"`{execution.playbook_name}` on `{execution.target_server}`.", key=f"confirm_{key}")
        comment = st.text_input("Comment / reason", key=f"comment_{key}",
                                placeholder="Required when rejecting")
        approve_col, reject_col = st.columns(2)
        if approve_col.button("Approve", type="primary", icon=":material/check_circle:", key=f"approve_{key}",
                              disabled=not confirmed, width="stretch"):
            try:
                app.approvals.approve(key, approver=user, comment=comment, confirmed=confirmed)
                refresh()
                st.success("Approved. Run it from **Executions**.")
                st.rerun()
            except OpsAgentXError as error:
                show_error(error)
        if reject_col.button("Reject", icon=":material/cancel:", key=f"reject_{key}", width="stretch"):
            try:
                app.approvals.reject(key, approver=user, reason=comment)
                refresh()
                st.rerun()
            except OpsAgentXError as error:
                show_error(error)

st.subheader("Approval history")
history = executions_df()
if history.empty:
    st.caption("No approval decisions yet.")
else:
    decided = history[history["Approval"] != "PENDING"]
    st.dataframe(
        decided[["Incident", "Playbook", "Target", "Approval", "Approved by", "Comment", "Execution", "Created"]],
        hide_index=True, width="stretch",
        column_config={"Created": st.column_config.DatetimeColumn("Requested", format="YYYY-MM-DD HH:mm")},
    )
