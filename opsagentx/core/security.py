"""Security policy engine.

This is the gate between "the AI suggested something" and "something may run".
It is deterministic Python - no LLM is involved in the decision.

Rules enforced (each produces a readable message):
  R1  The playbook must exist in the PLAYBOOKS allowlist and be enabled.
  R2  The playbook must be meant for the incident's category.
  R3  The playbook must be allowed in the target environment.
  R4  The target server must exist in the SERVERS inventory.
  R5  The target server must have ALLOW_AUTOMATION = TRUE.
  R6  The incident's environment must match the server's real environment
      (stops an incident payload from claiming 'development' for a prod box).
  R7  Human approval is ALWAYS required; production can never waive it.
  R8  Free-text recommendations containing shell commands are flagged and
      never executed - only the named playbook can ever run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core.config import settings
from database.repositories import InventoryRepository
from llm.schemas import NO_PLAYBOOK

# Patterns that look like someone (or a model) is trying to smuggle a command.
_SHELL_PATTERNS = [
    r"\brm\s+-", r"\bsudo\b", r"\bchmod\b", r"\bchown\b", r"\bdd\s+if=", r"\bmkfs",
    r"\bcurl\b.*\|", r"\bwget\b", r"\bkill\s+-9", r"\bshutdown\b", r"\breboot\b",
    r"&&", r"\|\|", r"`", r"\$\(",
]
_SHELL_REGEX = re.compile("|".join(_SHELL_PATTERNS), re.IGNORECASE)


@dataclass
class PolicyDecision:
    allowed: bool
    playbook_name: Optional[str]
    server_name: str
    environment: str
    requires_approval: bool = True
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "playbook_name": self.playbook_name,
            "server_name": self.server_name,
            "environment": self.environment,
            "requires_approval": self.requires_approval,
            "messages": self.messages,
            "warnings": self.warnings,
        }


def contains_shell_command(text: str) -> bool:
    return bool(text and _SHELL_REGEX.search(text))


class PolicyEngine:
    def __init__(self, inventory: InventoryRepository) -> None:
        self.inventory = inventory

    def validate_remediation(
        self,
        playbook_name: Optional[str],
        server_name: str,
        environment: str,
        category: Optional[str] = None,
        recommended_action: str = "",
    ) -> PolicyDecision:
        decision = PolicyDecision(
            allowed=True, playbook_name=playbook_name, server_name=server_name, environment=environment
        )

        def deny(message: str) -> None:
            decision.allowed = False
            decision.messages.append(message)

        # R1-R3: playbook checks
        if not playbook_name or playbook_name == NO_PLAYBOOK:
            deny("R1: no playbook recommended; nothing can be executed")
        else:
            playbooks = {p.playbook_name: p for p in self.inventory.list_playbooks()}
            playbook = playbooks.get(playbook_name)
            if playbook is None:
                deny(f"R1: playbook '{playbook_name}' is not in the approved playbook list")
            elif not playbook.enabled:
                deny(f"R1: playbook '{playbook_name}' is disabled")
            else:
                if category and playbook.category.value != category:
                    deny(f"R2: playbook '{playbook_name}' is for '{playbook.category.value}', "
                         f"not '{category}'")
                if environment not in [e.value for e in playbook.allowed_environments]:
                    deny(f"R3: playbook '{playbook_name}' is not allowed in '{environment}'")

        # R4-R6: target server checks
        server = self.inventory.get_server(server_name)
        if server is None:
            deny(f"R4: server '{server_name}' is not in the approved inventory")
        else:
            if not server.allow_automation:
                deny(f"R5: server '{server_name}' does not allow automated remediation")
            if server.environment.value != environment:
                deny(f"R6: incident says '{environment}' but '{server_name}' is "
                     f"'{server.environment.value}'")

        # R7: approval is never optional in this demo, and never in production.
        decision.requires_approval = (
            environment in settings.approval_required_environments or environment == "production"
        )

        # R8: commands in free text are never executed - flag them for the reviewer.
        if contains_shell_command(recommended_action):
            decision.warnings.append(
                "R8: recommended_action contains shell-like text; it is informational only "
                "and will NOT be executed"
            )

        if decision.allowed:
            decision.messages.append("all policy checks passed; human approval required")
        return decision
