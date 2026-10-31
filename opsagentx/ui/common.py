"""Shared Streamlit helpers: Snowflake session, service wiring, formatting.

All pages import from here so the object graph (repository, services, agent
runner, ...) is built once per app process and reused across reruns.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from services.factory import OpsAgentX, build_snowflake_app

STATUS_COLORS = {
    "OPEN": "red", "DIAGNOSED": "orange", "PENDING_APPROVAL": "violet", "APPROVED": "blue",
    "REJECTED": "gray", "REMEDIATING": "blue", "RESOLVED": "green", "REMEDIATION_FAILED": "red",
}
SEVERITY_COLORS = {"critical": "red", "high": "orange", "medium": "blue", "low": "gray"}
EXECUTION_COLORS = {
    "SUCCEEDED": "green", "FAILED": "red", "TIMED_OUT": "red", "VERIFICATION_FAILED": "orange",
    "CANCELLED": "gray", "RUNNING": "blue", "NOT_STARTED": "violet",
}
ALL = "All"


# ---------------------------------------------------------------------------
# Session and services
# ---------------------------------------------------------------------------
def _snowpark_session():
    """Snowpark session from the Streamlit Snowflake connection.

    Inside Streamlit in Snowflake this is the app's embedded, already
    authenticated session. The fallback exists only for headless testing in
    the development sandbox, where no Streamlit connection is configured.
    """
    try:
        return st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL")).session()
    except Exception:  # noqa: BLE001 - outside Snowflake only
        from database.connection import get_session

        return get_session()


@st.cache_resource(show_spinner="Connecting to Snowflake...")
def get_app() -> OpsAgentX:
    """Build all OpsAgentX services once per app process."""
    return build_snowflake_app(_snowpark_session())


def current_user() -> str:
    """The person using the app. Used as the actor for approvals and audit.

    st.user.user_name is the viewer's Snowflake username in Streamlit in
    Snowflake; it cannot be typed in, so approvals cannot be impersonated.
    """
    try:
        name = st.user.user_name  # type: ignore[attr-defined]
        if name:
            return str(name)
    except Exception:  # noqa: BLE001
        pass
    row = get_app().repository.session.sql("SELECT CURRENT_USER() AS U").collect()[0]
    return row["U"]


def refresh() -> None:
    """Drop cached reads after any write so every page shows fresh data."""
    st.cache_data.clear()


# ---------------------------------------------------------------------------
# Cached reads (short TTL; cleared after every action)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def incidents_df() -> pd.DataFrame:
    rows = [
        {
            "Incident": i.incident_id, "Severity": i.severity.value, "Status": i.status.value,
            "Category": i.category.value, "Server": i.server_name, "Environment": i.environment.value,
            "Source": i.source.value, "Description": i.description,
            "Created": i.created_at, "Updated": i.updated_at,
        }
        for i in get_app().incidents.list()
    ]
    return pd.DataFrame(rows)


@st.cache_data(ttl=60, show_spinner=False)
def executions_df(incident_id: str | None = None) -> pd.DataFrame:
    rows = [
        {
            "Incident": e.incident_id, "Playbook": e.playbook_name, "Target": e.target_server,
            "Approval": e.approval_status.value, "Approved by": e.approved_by,
            "Execution": e.execution_status.value,
            "Verified": None if e.verification_result is None else e.verification_result.get("passed"),
            "Created": e.created_at, "Completed": e.completed_at, "Comment": e.approval_comment,
            "Execution ID": e.execution_id,
        }
        for e in get_app().repository.list_executions(incident_id)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Small display helpers
# ---------------------------------------------------------------------------
def badge(text: str, palette: dict[str, str]) -> str:
    """Markdown colored badge, e.g. :red-badge[OPEN]."""
    return f":{palette.get(text, 'gray')}-badge[{text}]"


def incident_picker(label: str, statuses: Iterable[str] | None = None, key: str = "incident") -> str | None:
    """Selectbox of incidents (optionally filtered by status) that remembers the last choice."""
    df = incidents_df()
    if statuses is not None:
        df = df[df["Status"].isin(list(statuses))]
    if df.empty:
        return None
    options = df["Incident"].tolist()
    labels = {row.Incident: f"{row.Incident} · {row.Severity} · {row.Category} · {row.Server} · {row.Status}"
              for row in df.itertuples()}
    selected = st.session_state.get("selected_incident")
    index = options.index(selected) if selected in options else 0
    choice = st.selectbox(label, options, index=index, format_func=labels.get, key=key)
    st.session_state.selected_incident = choice
    return choice


def show_error(error: Exception) -> None:
    """Explain a refused or failed action without hiding what happened."""
    st.error(f"**{type(error).__name__}:** {error}", icon=":material/block:")


def as_dict(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value
