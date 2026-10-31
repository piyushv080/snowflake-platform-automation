"""Incident management: filter, inspect, ingest, and hand off to diagnosis."""
import pandas as pd
import streamlit as st

from core.exceptions import IncidentValidationError
from core.models import Category, Environment, IncidentStatus, Severity, Source
from ui.common import (
    ALL,
    SEVERITY_COLORS,
    STATUS_COLORS,
    badge,
    current_user,
    executions_df,
    get_app,
    incidents_df,
    refresh,
    show_error,
)

app = get_app()
df = incidents_df()

# --- Filters --------------------------------------------------------------------
with st.container(horizontal=True):
    severity = st.selectbox("Severity", [ALL] + [s.value for s in Severity])
    environment = st.selectbox("Environment", [ALL] + [e.value for e in Environment])
    category = st.selectbox("Category", [ALL] + [c.value for c in Category])
    status = st.selectbox("Status", [ALL] + [s.value for s in IncidentStatus])

filtered = df
for column, value in (("Severity", severity), ("Environment", environment),
                      ("Category", category), ("Status", status)):
    if value != ALL and not filtered.empty:
        filtered = filtered[filtered[column] == value]

st.caption(f"{len(filtered)} of {len(df)} incidents. Select a row to see details.")
selection = st.dataframe(
    filtered[["Incident", "Severity", "Status", "Category", "Server", "Environment", "Source",
              "Description", "Created"]] if not filtered.empty else filtered,
    hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="incident_table",
    column_config={"Created": st.column_config.DatetimeColumn("Created", format="YYYY-MM-DD HH:mm"),
                   "Description": st.column_config.TextColumn("Description", width="large")},
)

rows = selection.selection.rows if selection and selection.selection else []
if rows:
    st.session_state.selected_incident = filtered.iloc[rows[0]]["Incident"]

# --- Detail ------------------------------------------------------------------------
incident_id = st.session_state.get("selected_incident")
if incident_id and incident_id in set(df["Incident"]):
    incident = app.incidents.get(incident_id)
    server = app.repository.get_server(incident.server_name)

    with st.container(border=True):
        head, action = st.columns([4, 1], vertical_alignment="center")
        head.subheader(f"{incident.incident_id} · {incident.server_name}")
        head.markdown(
            f"{badge(incident.status.value, STATUS_COLORS)} {badge(incident.severity.value, SEVERITY_COLORS)} "
            f":gray-badge[{incident.category.value}] :gray-badge[{incident.environment.value}] "
            f":gray-badge[source: {incident.source.value}]"
        )
        if action.button("Diagnose with AI", type="primary", icon=":material/psychology:", width="stretch"):
            st.switch_page("app_pages/diagnosis.py")
        st.write(incident.description)

        info, srv = st.columns(2)
        info.markdown(f"**Created:** {incident.created_at:%Y-%m-%d %H:%M}  \n"
                      f"**Updated:** {incident.updated_at:%Y-%m-%d %H:%M}")
        if server:
            automation = ":green[allowed]" if server.allow_automation else ":red[not allowed]"
            srv.markdown(f"**Server:** {server.server_name} ({server.os}) - {server.role_description}  \n"
                         f"**Inventory environment:** {server.environment.value} · **Automation:** {automation}")
        else:
            srv.warning("Server is not in the approved inventory.")

        logs_tab, metrics_tab, history_tab = st.tabs(["Logs", "Metrics", "Remediation history"])
        with logs_tab:
            logs = app.repository.get_logs(incident.server_name, limit=50)
            st.dataframe(
                pd.DataFrame([{"Time": l.log_timestamp, "Level": l.log_level, "Message": l.log_message,
                               "Incident": l.incident_id} for l in logs]),
                hide_index=True, width="stretch",
                column_config={"Time": st.column_config.DatetimeColumn("Time", format="HH:mm:ss")},
            )
        with metrics_tab:
            metrics = pd.DataFrame([m.model_dump() for m in app.repository.get_metrics(incident.server_name)])
            if metrics.empty:
                st.caption("No metrics.")
            else:
                metrics["series"] = metrics["metric_name"] + metrics["mount_point"].fillna("").map(
                    lambda m: f" ({m})" if m else "")
                for series, group in metrics.groupby("series"):
                    latest = group.sort_values("metric_timestamp").iloc[-1]["metric_value"]
                    st.markdown(f"**{series}** - latest {latest:g}")
                    st.line_chart(group.set_index("metric_timestamp")["metric_value"], height=140)
        with history_tab:
            history = executions_df(incident.incident_id)
            if history.empty:
                st.caption("No remediation attempts yet.")
            else:
                st.dataframe(history.drop(columns=["Execution ID"]), hide_index=True, width="stretch")

# --- Ingest a new incident -----------------------------------------------------------------
with st.expander("Report a new incident", icon=":material/add_alert:"):
    st.caption("Simulates an incoming event from ServiceNow / monitoring. Input is validated and normalized "
               "(for example 'P1' becomes critical, 'prod' becomes production).")
    with st.form("new_incident", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        new_id = c1.text_input("Incident ID", placeholder="INC2001")
        new_source = c2.selectbox("Source", [s.value for s in Source])
        new_server = c3.text_input("Server", placeholder="server-01")
        c4, c5, c6 = st.columns(3)
        new_env = c4.text_input("Environment", placeholder="development / dev / prod")
        new_sev = c5.text_input("Severity", placeholder="high / P1 / sev2")
        new_cat = c6.text_input("Category", placeholder="disk_space / cpu / service")
        new_desc = st.text_area("Description", placeholder="What happened?")
        submitted = st.form_submit_button("Ingest incident", icon=":material/send:")
    if submitted:
        try:
            created = app.incidents.ingest(
                {"incident_id": new_id, "source": new_source, "server_name": new_server, "environment": new_env,
                 "severity": new_sev, "category": new_cat, "description": new_desc},
                actor=current_user(),
            )
            refresh()
            st.session_state.selected_incident = created.incident_id
            st.success(f"Created {created.incident_id} ({created.severity.value}, {created.category.value}, "
                       f"{created.environment.value}).")
            st.rerun()
        except IncidentValidationError as error:
            show_error(error)
