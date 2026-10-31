"""Diagnosis service: workflow steps 4-8.

Pipeline ("direct_llm" orchestration):
  1. Collect evidence with the controlled tools (logs, metrics, history, runbooks).
  2. Build a prompt that separates instructions from data.
  3. Ask the LLM provider for schema-constrained JSON.
  4. Validate the JSON with Pydantic (malformed / missing fields -> error).
  5. Cross-check the answer against facts we already know.
  6. Run the deterministic policy engine on the recommended playbook.
  7. Store the diagnosis and audit every step.

The LLM output is a *suggestion*. Nothing here executes anything.
"agent" orchestration (Stage 6): a Cortex Agent chooses and calls the tools
itself (steps 1-3). Steps 4-7 are IDENTICAL for both modes - the agent's answer
is validated and policy-checked exactly like the direct LLM answer.

mode="auto" tries the agent first and falls back to direct_llm on failure,
recording the fallback in the audit trail.
"""
from __future__ import annotations

import json
from typing import Any

from typing import Optional

from agent.instructions import DIAGNOSIS_SYSTEM_PROMPT
from agent.orchestration import AgentRunner
from agent.tools import DiagnosticTools
from core.config import settings
from core.exceptions import InvalidStateTransitionError, LLMProviderError, LLMResponseError
from core.models import AuditAction, Category, Diagnosis, Incident, IncidentStatus
from core.security import PolicyEngine
from database.repositories import OpsRepository
from llm.base import LLMProvider
from llm.schemas import DiagnosisResult, diagnosis_json_schema, parse_diagnosis
from services.audit_service import AuditService
from services.incident_service import IncidentService

ACTOR = "opsagentx-diagnosis"

# Statuses from which a (re-)diagnosis may start.
_DIAGNOSABLE = {
    IncidentStatus.OPEN, IncidentStatus.DIAGNOSED,
    IncidentStatus.REJECTED, IncidentStatus.REMEDIATION_FAILED,
}


class DiagnosisService:
    def __init__(
        self,
        repository: OpsRepository,
        incidents: IncidentService,
        tools: DiagnosticTools,
        policy: PolicyEngine,
        llm: LLMProvider,
        audit: AuditService,
        agent_runner: Optional[AgentRunner] = None,
    ) -> None:
        self.repository = repository
        self.incidents = incidents
        self.tools = tools
        self.policy = policy
        self.llm = llm
        self.audit = audit
        self.agent_runner = agent_runner

    # ------------------------------------------------------------------
    def collect_evidence(self, incident: Incident) -> tuple[dict[str, Any], list[str]]:
        """Call the relevant tools for this incident. Returns (evidence, tools_used)."""
        evidence: dict[str, Any] = {}
        tools_used: list[str] = []

        def call(tool_name: str, **kwargs: Any) -> Any:
            result = getattr(self.tools, tool_name)(**kwargs)
            tools_used.append(tool_name)
            self.audit.record(AuditAction.TOOL_CALLED, ACTOR, incident.incident_id, tool=tool_name, args=kwargs)
            return result

        evidence["incident"] = call("get_incident_details", incident_id=incident.incident_id)
        evidence["logs"] = call("get_server_logs", server_name=incident.server_name, limit=20)
        evidence["metrics"] = call("get_server_metrics", server_name=incident.server_name)
        evidence["history"] = call("get_historical_incidents", category=incident.category.value)

        # Category-specific tool selection (the agent version decides this itself).
        if incident.category == Category.DISK_SPACE:
            evidence["disk_analysis"] = call("analyze_disk_usage", server_name=incident.server_name)

        evidence["runbooks"] = call(
            "search_runbooks",
            query=f"{incident.category.value.replace('_', ' ')} {incident.description}",
            category=incident.category.value,
        )
        self.audit.record(
            AuditAction.RUNBOOKS_RETRIEVED, ACTOR, incident.incident_id,
            retriever=evidence["runbooks"]["retriever"],
            runbooks=[r["runbook_id"] for r in evidence["runbooks"]["runbooks"]],
        )
        return evidence, tools_used

    def build_prompt(self, incident: Incident, evidence: dict[str, Any], allowed_playbooks: list[str]) -> str:
        """Readable, compact prompt. Long fields are trimmed to keep token use bounded."""
        logs = [
            f"[{l['log_timestamp']}] {l['log_level']}: {l['log_message']}"
            for l in evidence["logs"]["logs"]
        ]
        metrics = [
            f"{m['metric_name']}={m['metric_value']}"
            + (f" ({m['mount_point']})" if m.get("mount_point") else "")
            + f" @ {m['metric_timestamp']}"
            for m in evidence["metrics"]["metrics"]
        ]
        runbooks = [
            f"--- {r['runbook_id']}: {r['title']} ---\n{r['content'][:1800]}"
            for r in evidence["runbooks"]["runbooks"]
        ]
        sections = {
            "INCIDENT": json.dumps(evidence["incident"]["incident"], indent=1),
            "SERVER": json.dumps(evidence["incident"]["server"], indent=1),
            "RECENT LOGS (newest first)": "\n".join(logs) or "(none)",
            "METRICS": "\n".join(metrics) or "(none)",
            "DISK ANALYSIS": json.dumps(evidence.get("disk_analysis"), indent=1) if "disk_analysis" in evidence else "(not run)",
            "SIMILAR RESOLVED INCIDENTS": json.dumps(evidence["history"]["incidents"], indent=1),
            "APPROVED RUNBOOKS": "\n\n".join(runbooks) or "(none found)",
            "ALLOWED PLAYBOOK NAMES": ", ".join(allowed_playbooks),
        }
        return "\n\n".join(f"## {title}\n{body}" for title, body in sections.items())

    # ------------------------------------------------------------------
    def diagnose(self, incident_id: str, actor: str = "operator", mode: str = "direct") -> Diagnosis:
        """Diagnose an incident. mode: 'direct' | 'agent' | 'auto' (agent, then direct)."""
        if mode not in ("direct", "agent", "auto"):
            raise ValueError(f"unknown diagnosis mode {mode!r}")
        incident = self.incidents.get(incident_id)
        if incident.status not in _DIAGNOSABLE:
            raise InvalidStateTransitionError(
                f"{incident_id} is {incident.status.value}; diagnosis is only allowed from "
                + ", ".join(s.value for s in _DIAGNOSABLE)
            )
        if mode in ("agent", "auto") and self.agent_runner is None:
            if mode == "agent":
                raise LLMProviderError("agent mode requested but no agent runner is configured")
            mode = "direct"

        self.audit.record(AuditAction.DIAGNOSIS_REQUESTED, actor, incident_id, mode=mode,
                          provider=self.agent_runner.name if mode != "direct" else self.llm.name)

        if mode in ("agent", "auto"):
            try:
                return self._diagnose_with_agent(incident)
            except (LLMProviderError, LLMResponseError) as error:
                self.audit.record(AuditAction.DIAGNOSIS_FAILED, ACTOR, incident_id, mode="cortex_agent",
                                  error_type=type(error).__name__, error=str(error)[:1000],
                                  fallback="direct_llm" if mode == "auto" else None)
                if mode == "agent":
                    raise
        return self._diagnose_direct(incident)

    # ------------------------------------------------------------------
    def _diagnose_direct(self, incident: Incident) -> Diagnosis:
        evidence, tools_used = self.collect_evidence(incident)
        allowed_playbooks = [p.playbook_name for p in self.repository.list_playbooks() if p.enabled]
        prompt = self.build_prompt(incident, evidence, allowed_playbooks)
        try:
            response = self.llm.generate_json(
                system_prompt=DIAGNOSIS_SYSTEM_PROMPT,
                user_prompt=prompt,
                json_schema=diagnosis_json_schema(allowed_playbooks),
            )
            result = parse_diagnosis(response.text, response.parsed)
        except (LLMProviderError, LLMResponseError) as error:
            self.audit.record(AuditAction.DIAGNOSIS_FAILED, ACTOR, incident.incident_id, mode="direct_llm",
                              error_type=type(error).__name__, error=str(error)[:1000])
            raise
        diagnosis = self._finalize(
            incident, result, response.text, response.model, tools_used,
            runbooks_used=[r["runbook_id"] for r in evidence["runbooks"]["runbooks"]],
            orchestration_mode="direct_llm",
        )
        return self._store(incident, diagnosis, usage=response.usage)

    def _diagnose_with_agent(self, incident: Incident) -> Diagnosis:
        run = self.agent_runner.run(incident.incident_id)
        for call in run.tool_calls:
            self.audit.record(AuditAction.TOOL_CALLED, "opsagentx-agent", incident.incident_id,
                              tool=call.name, args=call.arguments)
        self.audit.record(AuditAction.RUNBOOKS_RETRIEVED, "opsagentx-agent", incident.incident_id,
                          retriever="cortex_search (agent tool)", runbooks=run.runbook_ids)
        if run.unexpected_tools:
            # Cannot happen with our agent spec; if it ever does, refuse the result.
            raise LLMResponseError(f"agent used non-allowlisted tools: {run.unexpected_tools}")

        result = parse_diagnosis(run.final_text)   # raises LLMResponseError on bad output
        diagnosis = self._finalize(
            incident, result, run.final_text, run.model, run.tool_names,
            runbooks_used=run.runbook_ids, orchestration_mode="cortex_agent",
        )
        return self._store(incident, diagnosis, usage=run.usage)

    def _store(self, incident: Incident, diagnosis: Diagnosis, usage: dict) -> Diagnosis:
        diagnosis_id = self.repository.save_diagnosis(diagnosis)
        diagnosis = diagnosis.model_copy(update={"diagnosis_id": diagnosis_id})
        self.audit.record(
            AuditAction.DIAGNOSIS_COMPLETED, ACTOR, incident.incident_id,
            diagnosis_id=diagnosis_id, mode=diagnosis.orchestration_mode, model=diagnosis.ai_model,
            confidence=diagnosis.confidence, playbook=diagnosis.recommended_playbook,
            tools=diagnosis.tools_used, usage=usage,
        )
        self.audit.record(
            AuditAction.POLICY_VALIDATED if diagnosis.policy_valid else AuditAction.POLICY_REJECTED,
            "policy-engine", incident.incident_id, diagnosis_id=diagnosis_id, messages=diagnosis.policy_messages,
        )
        if incident.status != IncidentStatus.DIAGNOSED:
            self.incidents.change_status(incident.incident_id, IncidentStatus.DIAGNOSED, ACTOR,
                                         reason="diagnosis completed")
        return diagnosis

    def _finalize(self, incident: Incident, result: DiagnosisResult, raw_text: str, model: str,
                  tools_used: list[str], runbooks_used: list[str], orchestration_mode: str) -> Diagnosis:
        """Cross-check the model's claims and apply policy."""
        notes: list[str] = []

        # The model must not re-label the incident it was given.
        if result.incident_id.upper() != incident.incident_id:
            notes.append(f"model returned incident_id {result.incident_id!r}; corrected to {incident.incident_id}")
        if result.category != incident.category:
            notes.append(f"model suggested category {result.category.value!r}; "
                         f"incident category {incident.category.value!r} kept for policy checks")
        if not result.requires_human_approval:
            notes.append("model said no approval needed; overridden - approval is always required")
        if result.confidence < settings.min_confidence:
            notes.append(f"low confidence {result.confidence:.2f} (< {settings.min_confidence}); review carefully")

        decision = self.policy.validate_remediation(
            playbook_name=result.recommended_playbook,
            server_name=incident.server_name,
            environment=incident.environment.value,
            category=incident.category.value,
            recommended_action=result.recommended_action,
        )
        return Diagnosis(
            incident_id=incident.incident_id,
            root_cause=result.root_cause,
            confidence=result.confidence,
            recommended_action=result.recommended_action,
            recommended_playbook=result.recommended_playbook,
            requires_human_approval=True,
            policy_valid=decision.allowed,
            policy_messages=decision.messages + decision.warnings + notes,
            tools_used=tools_used,
            runbooks_used=runbooks_used,
            ai_model=model,
            orchestration_mode=orchestration_mode,
            raw_response=raw_text[:16000],
        )
