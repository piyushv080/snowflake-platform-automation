"""Controlled diagnostic tools.

These are the ONLY capabilities the AI side of OpsAgentX gets. Each tool:
* takes narrow, validated arguments (a server name, a category, ...),
* is READ-ONLY (no tool changes infrastructure or data),
* returns plain dicts that can be shown to an LLM or a human.

There is deliberately no "run_sql", "run_shell", or "execute_playbook" tool.
Execution happens only in the remediation service, after policy + approval.

The same functions back:
* the direct (deterministic) diagnosis pipeline (services/diagnosis_service.py),
* the Cortex Agent's custom tools (Stage 6, via SQL procedures),
* future LangGraph tool nodes in the local version.
"""
from __future__ import annotations

from typing import Any

from core.config import settings
from core.models import Category, SERVER_NAME_PATTERN
from core.security import PolicyEngine
from database.repositories import OpsRepository
from services.knowledge_service import RunbookRetriever

TOOL_NAMES = [
    "get_incident_details",
    "get_server_logs",
    "get_server_metrics",
    "search_runbooks",
    "get_historical_incidents",
    "analyze_disk_usage",
    "validate_remediation",
]


def _check_server_name(server_name: str) -> str:
    server_name = (server_name or "").strip().lower()
    if not SERVER_NAME_PATTERN.match(server_name):
        raise ValueError(f"invalid server name: {server_name!r}")
    return server_name


class DiagnosticTools:
    def __init__(self, repository: OpsRepository, retriever: RunbookRetriever, policy: PolicyEngine) -> None:
        self.repository = repository
        self.retriever = retriever
        self.policy = policy

    def get_incident_details(self, incident_id: str) -> dict[str, Any]:
        incident = self.repository.get_incident(incident_id.strip().upper())
        if incident is None:
            return {"error": f"incident {incident_id} not found"}
        server = self.repository.get_server(incident.server_name)
        return {
            "incident": incident.model_dump(mode="json"),
            "server": server.model_dump(mode="json") if server else None,
        }

    def get_server_logs(self, server_name: str, limit: int = settings.log_lookback_limit) -> dict[str, Any]:
        server_name = _check_server_name(server_name)
        logs = self.repository.get_logs(server_name, limit=min(int(limit), 200))
        return {"server_name": server_name, "logs": [log.model_dump(mode="json") for log in logs]}

    def get_server_metrics(self, server_name: str) -> dict[str, Any]:
        server_name = _check_server_name(server_name)
        metrics = self.repository.get_metrics(server_name)
        return {"server_name": server_name, "metrics": [m.model_dump(mode="json") for m in metrics]}

    def search_runbooks(self, query: str, category: str | None = None, limit: int = 3) -> dict[str, Any]:
        hits = self.retriever.search(query[:500], category=category, limit=min(int(limit), 5))
        return {
            "retriever": self.retriever.name,
            "runbooks": [
                {"runbook_id": h.runbook_id, "title": h.title, "category": h.category,
                 "score": h.score, "content": h.content}
                for h in hits
            ],
        }

    def get_historical_incidents(self, category: str, limit: int = 5) -> dict[str, Any]:
        category = Category(category).value  # raises ValueError on unknown categories
        incidents = self.repository.get_historical_incidents(category, limit=min(int(limit), 10))
        return {
            "category": category,
            "incidents": [
                {"incident_id": i.incident_id, "server_name": i.server_name, "description": i.description}
                for i in incidents
            ],
        }

    def analyze_disk_usage(self, server_name: str) -> dict[str, Any]:
        """Summarize disk metrics: latest usage per mount, trend, and /tmp size."""
        server_name = _check_server_name(server_name)
        metrics = self.repository.get_metrics(server_name)
        disk = sorted((m for m in metrics if m.metric_name == "disk_used_pct"), key=lambda m: m.metric_timestamp)
        tmp = [m for m in metrics if m.metric_name == "tmp_dir_gb"]
        if not disk:
            return {"server_name": server_name, "error": "no disk metrics available"}

        by_mount: dict[str, list[float]] = {}
        for metric in disk:
            by_mount.setdefault(metric.mount_point or "/", []).append(metric.metric_value)

        mounts = []
        for mount, values in by_mount.items():
            latest = values[-1]
            status = "critical" if latest >= 90 else "warning" if latest >= 80 else "ok"
            mounts.append({
                "mount_point": mount,
                "latest_used_pct": latest,
                "change_pct_points": round(latest - values[0], 1),
                "status": status,
            })
        return {
            "server_name": server_name,
            "mounts": mounts,
            "tmp_dir_gb": tmp[-1].metric_value if tmp else None,
            "likely_cause": "temporary directory growth" if tmp and tmp[-1].metric_value > 10 else "unknown",
        }

    def validate_remediation(self, playbook_name: str, environment: str, server_name: str = "",
                             category: str | None = None) -> dict[str, Any]:
        return self.policy.validate_remediation(
            playbook_name=playbook_name, server_name=server_name, environment=environment, category=category,
        ).to_dict()
