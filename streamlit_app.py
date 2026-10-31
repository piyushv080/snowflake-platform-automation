"""OpsAgentX - AI-assisted infrastructure operations (Streamlit in Snowflake).

Entry point: sets up the page, shared state, and navigation. Each page lives
in app_pages/ and uses ui/common.py to reach the OpsAgentX services.

Business logic is NOT in the UI: pages only call services (incidents,
diagnosis, approvals, remediation, audit), which enforce validation,
policy, approval, and audit rules on their own.
"""
import streamlit as st

st.set_page_config(page_title="OpsAgentX", page_icon=":material/hub:", layout="wide")

if "selected_incident" not in st.session_state:
    st.session_state.selected_incident = "INC1001"

page = st.navigation(
    [
        st.Page("app_pages/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True),
        st.Page("app_pages/incidents.py", title="Incidents", icon=":material/warning:"),
        st.Page("app_pages/diagnosis.py", title="AI Diagnosis", icon=":material/psychology:"),
        st.Page("app_pages/approvals.py", title="Approvals", icon=":material/how_to_reg:"),
        st.Page("app_pages/executions.py", title="Executions", icon=":material/play_circle:"),
        st.Page("app_pages/audit.py", title="Audit", icon=":material/history:"),
    ],
    position="top",
)

header, note = st.columns([3, 2], vertical_alignment="bottom")
header.title(f"OpsAgentX · {page.title}", anchor=False)
note.caption(":material/shield: AI proposes, policy validates, a human approves. "
             "Ansible and ServiceNow are **simulated** in this demo.")

page.run()
