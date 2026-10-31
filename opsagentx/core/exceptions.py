"""Application-specific exceptions.

Using our own exception types lets the UI and services tell apart
"bad input" from "policy said no" from "the LLM returned garbage",
and react to each one differently.
"""


class OpsAgentXError(Exception):
    """Base class for every error raised by OpsAgentX code."""


class IncidentValidationError(OpsAgentXError):
    """Incoming incident data is missing fields or has invalid values."""


class NotFoundError(OpsAgentXError):
    """A requested record (incident, diagnosis, execution) does not exist."""


class InvalidStateTransitionError(OpsAgentXError):
    """An incident or execution was asked to move to a status it cannot reach."""


class LLMResponseError(OpsAgentXError):
    """The model returned something we could not parse or validate."""


class LLMProviderError(OpsAgentXError):
    """The model call itself failed (network, quota, unknown model, ...)."""


class PolicyViolationError(OpsAgentXError):
    """A proposed remediation broke a security rule. Never execute it."""


class ApprovalRequiredError(OpsAgentXError):
    """Execution was attempted without a recorded human approval."""
