"""Caller-side wiring helper for the dependency governor.

Migrated from the reference application's run driver and generalized.
The Ch.8.5 caller-side contract has three steps, packaged here:

  1. register_fan_in   - declare structure (task depends on parents);
  2. notify_terminal   - report every parent's terminal REAL status;
  3. wait_ready        - poll readiness, then submit.

Edge-writing discipline: dependency edges for a registered fan-in are
written by the governor itself through its dep_graph_store. Never
hand-write those edges elsewhere or the graph shows duplicates.

Failure discipline: notify with the real status even on failure. A
failed parent only casts a cancel VOTE; a multi-parent fan-in may then
never become ready nor be cancelled — callers should fail loudly
instead of hanging on wait_ready.
"""

from __future__ import annotations

import asyncio
from typing import Iterable

from ordigovernance.api.orchestration import (
    DependencyGovernorProtocol,
    TaskInitIO,
)


class DependencyWiring:
    """Packages the register/notify/ready call chain for one run."""

    def __init__(self, governor: DependencyGovernorProtocol, *,
                 task_io: TaskInitIO | None = None) -> None:
        self._governor = governor
        self._task_io = task_io

    @property
    def governor(self) -> DependencyGovernorProtocol:
        return self._governor

    async def register_fan_in(
        self,
        task_id: str,
        parents: Iterable[str],
        *,
        primary_parent: str | None = None,
        initialize: bool = False,
        initial_status: str = "pending",
    ) -> None:
        """Declare task_id's fan-in over parents.

        initialize=True also creates the hot record up front (requires
        task_io), matching the reference driver's fan-in setup.
        """
        if initialize:
            if self._task_io is None:
                raise RuntimeError("initialize=True requires task_io")
            await self._task_io.initialize_task(
                task_id, initial_status=initial_status
            )
        await self._governor.register_dependency(
            task_id, list(parents), primary_parent=primary_parent
        )

    async def notify_terminal(self, task_id: str, status: str) -> None:
        """Report one parent's terminal status (real, never synthesized)."""
        await self._governor.notify_task_terminal(task_id, status)

    async def wait_ready(self, task_id: str, *, timeout: float,
                         poll_interval: float = 0.3) -> str:
        """Poll until task_id's fan-in is satisfied; TimeoutError otherwise."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while task_id not in await self._governor.get_ready_tasks():
            if loop.time() > deadline:
                raise TimeoutError(f"{task_id} never became ready")
            await asyncio.sleep(poll_interval)
        return task_id