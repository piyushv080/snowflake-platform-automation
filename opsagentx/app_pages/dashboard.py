"""Dashboard: KPIs, status/category breakdown, recent executions, demo guide."""
import pandas as pd
import streamlit as st

from ui.common import EXECUTION_COLORS, executions_df, get_app, incidents_df, refresh

incidents = incidents_df()
executions = executions_df()

# --- KPIs -------------------------------------------------------------------
total = len(incidents)
open_mask = ~incidents["Status"].isin(["RESOLVED"]) if total else pd.Series(dtype=bool)
open_count = int(open_mask.sum()) if total else 0
critical_open = int((open_mask & (incidents["Severity"] == "critical")).sum()) if total else 0
resolved = int((incidents["Status"] == "RESOLVED").sum()) if total else 0
awaiting = int((incidents["Status"] == "PENDING_APPROVAL").sum()) if total else 0

with st.container(horizontal=True):
    st.metric("Total incidents", total, border=True)
    st.metric("Open incidents", open_count, border=True)
    st.metric("Critical open", critical_open, border=True)
    st.metric("Resolved", resolved, border=True)
    st.metric("Awaiting approval", awaiting, border=True)

# --- Breakdown charts ---------------------------------------------------------
left, right = st.columns(2)
with left.container(border=True):
    st.markdown("**Incidents by status**")
    if total:
        st.bar_chart(incidents.groupby("Status").size().rename("Incidents"), horizontal=True, height=260)
with right.container(border=True):
    st.markdown("**Open incidents by category and severity**")
    if open_count:
        pivot = (incidents[open_mask].groupby(["Category", "Severity"]).size()
                 .unstack(fill_value=0))
        st.bar_chart(pivot, height=260, stack=True)
    else:
        st.caption("No open incidents.")

# --- Recent executions ---------------------------------------------------------
with st.container(border=True):
    st.markdown("**Recent remediation executions**")
    if executions.empty:
        st.caption("No remediation has been requested yet. Start on **Incidents** or **AI Diagnosis**.")
    else:
        recent = executions.head(10).copy()
        recent["Execution"] = recent["Execution"].map(lambda s: f"{s}")
        st.dataframe(
            recent[["Incident", "Playbook", "Target", "Approval", "Approved by", "Execution", "Verified",
                    "Completed"]],
            hide_index=True, width="stretch",
            column_config={"Verified": st.column_config.CheckboxColumn("Verified"),
                           "Completed": st.column_config.DatetimeColumn("Completed", format="YYYY-MM-DD HH:mm")},
        )
        legend = "  ".join(f":{color}-badge[{name}]" for name, color in EXECUTION_COLORS.items())
        st.caption(legend)

# --- Demo guide + reset ---------------------------------------------------------
with st.expander("Demo walkthrough (INC1001 disk at 95% on server-01)", icon=":material/school:"):
    st.markdown(
        """
1. **Incidents** - open INC1001, review logs and metrics.
2. **AI Diagnosis** - run the Cortex Agent (or direct LLM); review root cause, runbooks and tools used.
3. **AI Diagnosis** - click *Request approval* (the policy engine re-checks the playbook and target).
4. **Approvals** - tick the confirmation box and approve `disk_cleanup.yml` (or reject with a reason).
5. **Executions** - run the **simulated** Ansible playbook and check verification (disk must drop below 80%).
6. **Audit** - review the complete timeline.

Other scenarios: INC1002 (high CPU), INC1003 (nginx stopped), INC1004 (production DB - blocked by policy).
        """
    )

with st.expander("Reset demo data", icon=":material/restart_alt:"):
    st.warning("Deletes all incidents, diagnoses, executions and audit records and reloads the sample data.")
    if st.checkbox("I understand this deletes all demo activity", key="confirm_reset"):
        if st.button("Reset demo data", type="primary", icon=":material/restart_alt:"):
            with st.spinner("Resetting..."):
                result = get_app().repository.session.sql(
                    "CALL OPSAGENTX_DB.OPSAGENTX_SCHEMA.RESET_DEMO_DATA()").collect()[0][0]
            refresh()
            st.success(result)
            st.rerun()
