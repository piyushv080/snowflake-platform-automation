"""AI diagnosis: run Cortex Agent / direct LLM and review the structured result."""
import json

import streamlit as st

from core.exceptions import OpsAgentXError
from ui.common import (
    STATUS_COLORS,
    badge,
    current_user,
    get_app,
    incident_picker,
    refresh,
    show_error,
)

app = get_app()
DIAGNOSABLE = ["OPEN", "DIAGNOSED", "REJECTED", "REMEDIATION_FAILED"]
MODES = {
    "Cortex Agent (agent chooses tools)": "agent",
    "Direct LLM (fixed tool pipeline + AI_COMPLETE)": "direct",
    "Auto (agent, fall back to direct)": "auto",
}

incident_id = incident_picker("Incident", key="diag_incident")
if incident_id is None:
    st.info("No incidents.")
    st.stop()
incident = app.incidents.get(incident_id)

# --- Run a diagnosis ---------------------------------------------------------------
with st.container(border=True):
    st.markdown(f"**{incident.incident_id}** on `{incident.server_name}` ({incident.environment.value}) "
                f"{badge(incident.status.value, STATUS_COLORS)}  \n{incident.description}")
    can_diagnose = incident.status.value in DIAGNOSABLE
    mode_label = st.radio("Orchestration", list(MODES), horizontal=True, disabled=not can_diagnose)
    st.caption("The agent typically takes 40-60 s (it plans and calls up to 7 read-only tools); "
               "direct mode takes about 15 s. Either way, the answer is validated and policy-checked before use.")
    if st.button("Run AI diagnosis", type="primary", icon=":material/psychology:", disabled=not can_diagnose):
        with st.spinner("Investigating incident..."):
            try:
                app.diagnosis.diagnose(incident_id, actor=current_user(), mode=MODES[mode_label])
                refresh()
                st.rerun()
            except OpsAgentXError as error:
                show_error(error)
    if not can_diagnose:
        st.caption(f"Diagnosis is available when the incident is {', '.join(DIAGNOSABLE)}.")

# --- Latest diagnosis ----------------------------------------------------------------------
diagnosis = app.repository.get_latest_diagnosis(incident_id)
if diagnosis is None:
    st.info("No diagnosis yet for this incident.")
    st.stop()

mode_text = "Cortex Agent" if diagnosis.orchestration_mode == "cortex_agent" else "Direct LLM"
st.subheader("Latest diagnosis")
st.caption(f"{mode_text} · model `{diagnosis.ai_model}` · {diagnosis.created_at:%Y-%m-%d %H:%M:%S}")

with st.container(horizontal=True):
    st.metric("Confidence", f"{diagnosis.confidence:.0%}", border=True)
    st.metric("Recommended playbook", diagnosis.recommended_playbook or "none", border=True)
    st.metric("Policy", "Allowed" if diagnosis.policy_valid else "Blocked", border=True)
    st.metric("Tools used", len(diagnosis.tools_used), border=True)

with st.container(border=True):
    st.markdown("**Root cause**")
    st.write(diagnosis.root_cause)
    st.markdown("**Recommended action** (for a human; never executed as text)")
    st.write(diagnosis.recommended_action)

    if diagnosis.policy_valid:
        st.success("Policy checks passed. Human approval is still required before anything runs.",
                   icon=":material/verified_user:")
    else:
        st.error("Policy engine blocked this remediation - it cannot be executed.", icon=":material/gpp_bad:")
    for message in diagnosis.policy_messages:
        st.markdown(f"- {message}")

tools_col, runbook_col = st.columns(2)
with tools_col.container(border=True):
    st.markdown("**Diagnostic tools used** (in call order)")
    st.markdown(" ".join(f":blue-badge[{tool}]" for tool in diagnosis.tools_used) or "_none_")
    try:
        evidence = json.loads(diagnosis.raw_response).get("evidence", [])
    except (json.JSONDecodeError, AttributeError):
        evidence = []
    if evidence:
        st.markdown("**Evidence cited by the model**")
        for item in evidence:
            st.markdown(f"- {item}")
with runbook_col.container(border=True):
    st.markdown("**Runbooks retrieved**")
    runbooks = {r.runbook_id: r for r in app.repository.list_runbooks()}
    for runbook_id in diagnosis.runbooks_used:
        runbook = runbooks.get(runbook_id)
        if runbook:
            with st.expander(f"{runbook.runbook_id} · {runbook.title}"):
                st.text(runbook.content)

with st.expander("Raw model output (audit/debug)", icon=":material/code:"):
    st.code(diagnosis.raw_response, language="json")

# --- Hand off to approval ---------------------------------------------------------------------
if incident.status.value == "DIAGNOSED":
    if diagnosis.policy_valid and diagnosis.recommended_playbook not in (None, "none"):
        if st.button(f"Request approval for {diagnosis.recommended_playbook} on {incident.server_name}",
                     type="primary", icon=":material/how_to_reg:"):
            try:
                app.approvals.request_approval(incident_id, requested_by=current_user())
                refresh()
                st.switch_page("app_pages/approvals.py")
            except OpsAgentXError as error:
                show_error(error)
    else:
        st.info("This diagnosis cannot be sent for approval. Escalate manually or re-diagnose.",
                icon=":material/support_agent:")
