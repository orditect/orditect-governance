"""Orchestration protocols (zero orditect imports).

DependencyGovernorProtocol pins the PASSIVE governor surface the
caller-side contract requires (Ch.8.5): the governor never creates
tasks and never schedules; the caller registers structure, notifies
terminal events with real statuses, and polls readiness.

Reference implementation: orditect.flow.governance.DependencyGovernor.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class DependencyGovernorProtocol(Protocol):
    """Passive dependency governor (structure + readiness only)."""

    async def register_dependency(
        self,
        task_id: str,
        parents: Iterable[str],
        *,
        primary_parent: str | None = None,
    ) -> None:
        """Register a fan-in: task_id depends on every parent."""
        ...

    async def notify_task_terminal(self, task_id: str, status: str) -> None:
        """Notify with the REAL terminal status (never a synthesized one)."""
        ...

    async def get_ready_tasks(self) -> list:
        """Return the ids whose fan-in conditions are fully satisfied."""
        ...


@runtime_checkable
class TaskInitIO(Protocol):
    """Optional hot-record initialization surface used by the wiring."""

    async def initialize_task(self, task_id: str, *,
                              initial_status: str = "pending") -> None: ...