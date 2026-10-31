"""Ansible integration layer.

`AnsibleClient` is the interface the remediation service uses. Two kinds of
implementation are intended:

* SimulatedAnsibleClient (here)  - the ONLY implementation in this project.
  It never connects to any host. It produces realistic job output and
  reports a simulated post-remediation server state.
* AAPClient (future)             - would call Ansible Automation Platform's
  REST API: POST /api/v2/job_templates/{id}/launch/, then poll
  GET /api/v2/jobs/{id}/ until finished, then read /stdout/. Credentials
  would come from a Snowflake SECRET + EXTERNAL ACCESS INTEGRATION (inside
  Snowflake) or environment variables (local version) - never from code.

The client only accepts a playbook NAME and a target host. It never accepts
shell commands, and callers have already checked policy and approval.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from core.exceptions import OpsAgentXError


class AnsibleTimeoutError(OpsAgentXError):
    """The job did not finish within the allowed time."""


class JobOutcome(str, Enum):
    SUCCESSFUL = "successful"
    FAILED = "failed"


@dataclass
class PostStateMetric:
    """A metric the (simulated) monitoring system would report after the job."""

    metric_name: str
    value: float
    mount_point: Optional[str] = None


@dataclass
class JobResult:
    job_id: str
    outcome: JobOutcome
    stdout: str
    elapsed_seconds: float
    post_state: list[PostStateMetric] = field(default_factory=list)


class AnsibleClient(ABC):
    name = "abstract"

    @abstractmethod
    def run_playbook(self, playbook_name: str, target_server: str, extra_vars: dict[str, Any],
                     timeout_seconds: int = 300) -> JobResult:
        """Run an approved playbook against one host.

        Returns a JobResult (outcome may be FAILED). Raises AnsibleTimeoutError on timeout.
        """


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------
class SimulationScenario(str, Enum):
    SUCCESS = "success"                            # job ok, problem fixed
    FAILURE = "failure"                            # job reports a failed task
    TIMEOUT = "timeout"                            # job never finishes
    VERIFICATION_FAILURE = "verification_failure"  # job "ok" but problem NOT fixed


# What each playbook "does" to the simulated server: (fixed_state, unfixed_state).
_EFFECTS: dict[str, tuple[list[PostStateMetric], list[PostStateMetric]]] = {
    "disk_cleanup.yml": (
        [PostStateMetric("disk_used_pct", 71.5, "/"), PostStateMetric("tmp_dir_gb", 1.2, "/tmp")],
        [PostStateMetric("disk_used_pct", 93.8, "/"), PostStateMetric("tmp_dir_gb", 17.9, "/tmp")],
    ),
    "restart_service.yml": (
        [PostStateMetric("service_up", 1.0, "nginx")],
        [PostStateMetric("service_up", 0.0, "nginx")],
    ),
    "restart_high_cpu_process.yml": (
        [PostStateMetric("cpu_pct", 34.0)],
        [PostStateMetric("cpu_pct", 95.0)],
    ),
    # Read-only diagnostic playbooks collect data but do not change the server.
    "cpu_diagnostics.yml": ([], []),
    "memory_diagnostics.yml": ([], []),
    "ansible_rerun_job.yml": ([], []),
}

_TASKS: dict[str, list[str]] = {
    "disk_cleanup.yml": [
        "Gather disk facts", "Find files older than 7 days in approved paths (/tmp, /var/tmp, /var/log/app/archive)",
        "Remove approved cleanup candidates", "Report freed space",
    ],
    "restart_service.yml": ["Validate service is on approved list", "Validate config (nginx -t)",
                            "Restart service", "Wait for service active"],
    "restart_high_cpu_process.yml": ["Identify approved process", "Graceful stop", "Start process",
                                     "Confirm process healthy"],
    "cpu_diagnostics.yml": ["Collect top snapshot", "Collect ps --sort=-%cpu", "Collect pidstat 5 3"],
    "memory_diagnostics.yml": ["Collect free -m", "Collect vmstat", "Collect ps --sort=-%mem"],
    "ansible_rerun_job.yml": ["Re-launch job template", "Wait for job"],
}


class SimulatedAnsibleClient(AnsibleClient):
    """Deterministic stand-in for Ansible. Choose the outcome with `scenario`."""

    name = "ansible_simulator"

    def __init__(self, scenario: SimulationScenario = SimulationScenario.SUCCESS,
                 simulated_duration_seconds: float = 0.5) -> None:
        self.scenario = SimulationScenario(scenario)
        self.simulated_duration_seconds = simulated_duration_seconds
        self.calls: list[dict[str, Any]] = []

    def run_playbook(self, playbook_name: str, target_server: str, extra_vars: dict[str, Any],
                     timeout_seconds: int = 300) -> JobResult:
        if playbook_name not in _EFFECTS:
            # Defense in depth: the simulator only knows allowlisted playbooks.
            raise ValueError(f"simulator has no playbook named {playbook_name!r}")
        self.calls.append({"playbook": playbook_name, "target": target_server, "extra_vars": extra_vars})

        started = time.time()
        time.sleep(self.simulated_duration_seconds)  # tiny pause so the UI shows a running state
        job_id = f"SIM-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

        if self.scenario == SimulationScenario.TIMEOUT:
            raise AnsibleTimeoutError(
                f"job {job_id}: no result from {target_server} within {timeout_seconds}s (simulated)")

        fixed, unfixed = _EFFECTS[playbook_name]
        tasks = _TASKS.get(playbook_name, ["Run playbook"])
        if self.scenario == SimulationScenario.FAILURE:
            stdout = self._render(playbook_name, target_server, tasks, failed_at=len(tasks) - 2)
            return JobResult(job_id, JobOutcome.FAILED, stdout, time.time() - started, post_state=unfixed)

        stdout = self._render(playbook_name, target_server, tasks, failed_at=None)
        post_state = unfixed if self.scenario == SimulationScenario.VERIFICATION_FAILURE else fixed
        return JobResult(job_id, JobOutcome.SUCCESSFUL, stdout, time.time() - started, post_state=post_state)

    @staticmethod
    def _render(playbook: str, host: str, tasks: list[str], failed_at: Optional[int]) -> str:
        """Produce Ansible-like stdout so operators see something familiar."""
        lines = [f"[SIMULATED] PLAY [{playbook}] on {host} " + "*" * 30]
        ok = changed = 0
        for index, task in enumerate(tasks):
            lines.append(f"TASK [{task}] " + "*" * 20)
            if failed_at is not None and index == max(failed_at, 0):
                lines.append(f'fatal: [{host}]: FAILED! => {{"msg": "simulated failure in task \'{task}\'"}}')
                lines.append(f"PLAY RECAP: {host} : ok={ok} changed={changed} unreachable=0 failed=1")
                return "\n".join(lines)
            is_change = task.lower().startswith(("remove", "restart", "graceful", "start"))
            lines.append(f"{'changed' if is_change else 'ok'}: [{host}]")
            ok += 1
            changed += int(is_change)
        lines.append(f"PLAY RECAP: {host} : ok={ok} changed={changed} unreachable=0 failed=0")
        return "\n".join(lines)
