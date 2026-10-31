"""Composition root: builds the object graph for the Snowflake deployment.

This is the ONE place that knows which concrete classes are used. Business
services receive their dependencies as constructor arguments, so the future
local version only needs its own factory (SQLiteRepository, OllamaProvider,
KeywordRunbookRetriever or a local vector store, ...).
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.orchestration import CortexAgentRunner
from agent.tools import DiagnosticTools
from core.config import settings
from core.security import PolicyEngine
from database.snowflake_repository import SnowflakeRepository
from integrations.ansible import SimulatedAnsibleClient
from integrations.servicenow import SimulatedServiceNowClient
from llm.snowflake_cortex import SnowflakeCortexProvider
from services.approval_service import ApprovalService
from services.audit_service import AuditService
from services.cortex_search_retriever import CortexSearchRetriever, FallbackRunbookRetriever
from services.diagnosis_service import DiagnosisService
from services.incident_service import IncidentService
from services.knowledge_service import KeywordRunbookRetriever
from services.remediation_service import RemediationService


@dataclass
class OpsAgentX:
    repository: SnowflakeRepository
    audit: AuditService
    policy: PolicyEngine
    retriever: FallbackRunbookRetriever
    tools: DiagnosticTools
    llm: SnowflakeCortexProvider
    agent_runner: CortexAgentRunner
    incidents: IncidentService
    diagnosis: DiagnosisService
    approvals: ApprovalService
    remediation: RemediationService


def build_snowflake_app(session) -> OpsAgentX:
    repository = SnowflakeRepository(session)
    audit = AuditService(repository)
    policy = PolicyEngine(repository)
    retriever = FallbackRunbookRetriever(
        primary=CortexSearchRetriever(session),
        fallback=KeywordRunbookRetriever(repository),
    )
    tools = DiagnosticTools(repository, retriever, policy)
    llm = SnowflakeCortexProvider(session, settings.llm_model, settings.llm_fallback_model)
    agent_runner = CortexAgentRunner(session, settings.fq(settings.agent_name))
    incidents = IncidentService(repository, audit)
    diagnosis = DiagnosisService(repository, incidents, tools, policy, llm, audit, agent_runner)
    approvals = ApprovalService(repository, incidents, policy, audit)
    # Simulated integrations only: nothing in this project touches real infrastructure.
    remediation = RemediationService(repository, incidents, policy, SimulatedAnsibleClient(), audit,
                                     tickets=SimulatedServiceNowClient())
    return OpsAgentX(repository, audit, policy, retriever, tools, llm, agent_runner, incidents, diagnosis,
                     approvals, remediation)
