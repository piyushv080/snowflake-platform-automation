"""ServiceNow integration layer (simulated).

The remediation service posts a work note when an incident is resolved or
remediation fails. `SimulatedServiceNowClient` only records notes in memory
and the caller audits them; it never calls ServiceNow.

A real implementation would PATCH /api/now/table/incident/{sys_id} with
`work_notes` and `state`, using credentials from a Snowflake SECRET via an
EXTERNAL ACCESS INTEGRATION (or environment variables locally).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TicketUpdate:
    incident_id: str
    state: str
    work_note: str


class TicketClient(ABC):
    name = "abstract"

    @abstractmethod
    def update_ticket(self, incident_id: str, state: str, work_note: str) -> TicketUpdate: ...


class SimulatedServiceNowClient(TicketClient):
    name = "servicenow_simulator"

    def __init__(self) -> None:
        self.updates: list[TicketUpdate] = []

    def update_ticket(self, incident_id: str, state: str, work_note: str) -> TicketUpdate:
        update = TicketUpdate(incident_id, state, work_note[:4000])
        self.updates.append(update)
        return update
