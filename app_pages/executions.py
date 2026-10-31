"""Execution center: run approved remediations (simulated Ansible) and verify."""
import streamlit as st

from core.exceptions import OpsAgentXError
from integrations.ansible import SimulatedAnsibleClient, SimulationScenario
from ui.common import EXECUTION_COLORS, badge, current_user, executions_df, get_app, refresh, show_error

app = get_app()

SCENARIOS = {
    "Success": SimulationScenario.SUCCESS,
    "Playbook failure": SimulationScenario.FAILURE,
    "Timeout": SimulationScenario.TIMEOUT,
    "Runs but problem persists (verification failure)": SimulationScenario.VERIFICATION_FAILURE,
}

st.info("Ansible is **simulated**. No host is contacted and nothing outside Snowflake changes. "
        "Choose the simulated outcome to demonstrate each path.", icon=":material/science:")

ready = [e for e in app.repository.list_executions()
         if e.approval_status.value == "APPROVED" and e.execution_status.value == "NOT_STARTED"]
if not ready:
    st.caption("No approved remediation is waiting to run.")

for execution in ready:
    with st.container(border=True):
        st.subheader(f"{execution.incident_id}: `{execution.playbook_name}` → `{execution.target_server}`")
        st.caption(f"Approved by {execution.approved_by} at {execution.approved_at:%Y-%m-%d %H:%M}"
                   + (f" - \"{execution.approval_comment}\"" if execution.approval_comment else ""))
        scenario = st.selectbox("Simulated outcome", list(SCENARIOS), key=f"scenario_{execution.execution_id}")
        if st.button("Run simulated playbook", type="primary", icon=":material/play_circle:",
                     key=f"run_{execution.execution_id}"):
            with st.spinner(f"Running {execution.playbook_name} (simulated)..."):
                try:
                    result = app.remediation.execute(
                        execution.execution_id, actor=current_user(),
                        ansible=SimulatedAnsibleClient(SCENARIOS[scenario]))
                    st.session_state.last_execution = result.execution_id
                    refresh()
                    st.rerun()
                except OpsAgentXError as error:
                    show_error(error)

# --- Result of the most recent / selected execution ----------------------------------------
history = executions_df()
finished = history[~history["Execution"].isin(["NOT_STARTED", "CANCELLED"])] if not history.empty else history
if not finished.empty:
    st.subheader("Execution results")
    options = finished["Execution ID"].tolist()
    labels = {row["Execution ID"]: f"{row['Incident']} · {row['Playbook']} · {row['Execution']}"
              for _, row in finished.iterrows()}
    last = st.session_state.get("last_execution")
    chosen = st.selectbox("Execution", options, index=options.index(last) if last in options else 0,
                          format_func=labels.get)
    execution = app.repository.get_execution(chosen)
    incident = app.incidents.get(execution.incident_id)

    with st.container(border=True):
        status = execution.execution_status.value
        st.markdown(f"**Execution:** {badge(status, EXECUTION_COLORS)}   **Incident now:** "
                    f":gray-badge[{incident.status.value}]")
        verification = execution.verification_result
        if status == "SUCCEEDED":
            st.success("Playbook succeeded **and** verification passed - incident resolved.",
                       icon=":material/task_alt:")
        elif status == "VERIFICATION_FAILED":
            st.warning("The playbook reported success, but the problem is still present. "
                       "The incident was NOT resolved.", icon=":material/report:")
        else:
            st.error(f"Remediation did not succeed ({status}). The incident was NOT resolved; "
                     "re-diagnose or escalate.", icon=":material/error:")

        if verification:
            st.markdown(f"**Verification rule:** `{verification.get('rule', 'n/a')}`")
            with st.container(horizontal=True):
                st.metric("Before", verification.get("before"), border=True)
                after = verification.get("after")
                before = verification.get("before")
                delta = round(after - before, 1) if isinstance(after, (int, float)) and isinstance(
                    before, (int, float)) else None
                st.metric("After", after, delta=delta, delta_color="inverse", border=True)
                st.metric("Verified", "yes" if verification.get("passed") else "no", border=True)
            if verification.get("reason"):
                st.caption(verification["reason"])
        st.markdown("**Ansible output (simulated)**")
        st.code(execution.execution_output or "(no output)", language="text")

st.subheader("Execution history")
if history.empty:
    st.caption("No executions yet.")
else:
    st.dataframe(history.drop(columns=["Execution ID"]), hide_index=True, width="stretch",
                 column_config={"Verified": st.column_config.CheckboxColumn("Verified")})
