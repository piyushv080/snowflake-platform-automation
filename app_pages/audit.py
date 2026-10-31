"""Audit history: chronological timeline of every action for an incident."""
import json

import pandas as pd
import streamlit as st

from ui.common import get_app, incident_picker

app = get_app()

ICONS = {
    "INCIDENT_CREATED": ":material/add_alert:", "INCIDENT_REJECTED_INVALID": ":material/block:",
    "STATUS_CHANGED": ":material/swap_horiz:", "DIAGNOSIS_REQUESTED": ":material/psychology:",
    "TOOL_CALLED": ":material/build:", "RUNBOOKS_RETRIEVED": ":material/menu_book:",
    "DIAGNOSIS_COMPLETED": ":material/fact_check:", "DIAGNOSIS_FAILED": ":material/error:",
    "POLICY_VALIDATED": ":material/verified_user:", "POLICY_REJECTED": ":material/gpp_bad:",
    "APPROVAL_REQUESTED": ":material/how_to_reg:", "APPROVAL_GRANTED": ":material/check_circle:",
    "APPROVAL_DENIED": ":material/cancel:", "EXECUTION_BLOCKED": ":material/do_not_disturb_on:",
    "EXECUTION_STARTED": ":material/play_circle:", "EXECUTION_FINISHED": ":material/stop_circle:",
    "VERIFICATION_PASSED": ":material/task_alt:", "VERIFICATION_FAILED": ":material/report:",
    "INCIDENT_RESOLVED": ":material/celebration:", "TICKET_UPDATED": ":material/confirmation_number:",
}
BAD = {"DIAGNOSIS_FAILED", "POLICY_REJECTED", "APPROVAL_DENIED", "EXECUTION_BLOCKED", "VERIFICATION_FAILED",
       "INCIDENT_REJECTED_INVALID"}
GOOD = {"POLICY_VALIDATED", "APPROVAL_GRANTED", "VERIFICATION_PASSED", "INCIDENT_RESOLVED"}


def summary(action: str, details: dict) -> str:
    """One readable line per event."""
    if action == "STATUS_CHANGED":
        return f"{details.get('from_status')} → **{details.get('to_status')}** {details.get('reason') or ''}"
    if action == "TOOL_CALLED":
        return f"`{details.get('tool')}` {json.dumps(details.get('args', {}))}"
    if action == "RUNBOOKS_RETRIEVED":
        return f"{', '.join(details.get('runbooks', []))} via {details.get('retriever')}"
    if action == "DIAGNOSIS_COMPLETED":
        return (f"`{details.get('playbook')}` confidence {details.get('confidence')} "
                f"({details.get('mode')}, {details.get('model')})")
    if action in ("POLICY_VALIDATED", "POLICY_REJECTED"):
        return "; ".join(details.get("messages", []))
    if action in ("APPROVAL_GRANTED", "APPROVAL_REQUESTED"):
        return f"`{details.get('playbook')}` on `{details.get('target')}` {details.get('comment') or ''}"
    if action == "APPROVAL_DENIED":
        return f"reason: {details.get('reason')}"
    if action == "EXECUTION_FINISHED":
        return f"**{details.get('status')}** {details.get('job_id', details.get('error', ''))}"
    if action.startswith("VERIFICATION"):
        return f"{details.get('rule')}: {details.get('before')} → {details.get('after')}"
    if action in ("DIAGNOSIS_FAILED", "EXECUTION_BLOCKED"):
        return str(details.get("error") or details.get("reason"))
    return ", ".join(f"{k}={v}" for k, v in details.items() if not isinstance(v, (dict, list)))[:200]


incident_id = incident_picker("Incident", key="audit_incident")
if incident_id is None:
    st.info("No incidents.")
    st.stop()

events = app.audit.timeline(incident_id)
actions = sorted({e.action.value for e in events})
chosen = st.multiselect("Show actions", actions, default=actions)
st.caption(f"{len(events)} audit records for {incident_id}. Audit rows are append-only.")

for event in events:
    action = event.action.value
    if action not in chosen:
        continue
    color = "red" if action in BAD else "green" if action in GOOD else "gray"
    with st.container(border=True):
        left, right = st.columns([3, 1])
        left.markdown(f"{ICONS.get(action, ':material/circle:')} :{color}[**{action}**]  \n"
                      f"{summary(action, event.details)}")
        right.markdown(f"`{event.created_at:%H:%M:%S}`  \n:gray[{event.actor}]")
        with st.expander("details"):
            st.json(event.details)

with st.expander("Export as table", icon=":material/table:"):
    st.dataframe(pd.DataFrame([{"Time": e.created_at, "Action": e.action.value, "Actor": e.actor,
                                "Details": json.dumps(e.details, default=str)} for e in events]),
                 hide_index=True, width="stretch")
