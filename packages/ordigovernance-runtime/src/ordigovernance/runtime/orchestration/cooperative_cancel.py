"""Cooperative cancellation utilities (HITL pause surface).

Migrated from the reference application's researcher unit. A node in a
cooperative delay window polls its own hot record and raises
CancelledError when an external pause action has set cancel_requested;
the node then settles as cancelled, and the supervisor's resume path
reruns it on a new generation.
"""

from __future__ import annotations

import asyncio

from ordigovernance.runtime.task.governed_task import TaskIO


async def raise_if_cancelled(task_io: TaskIO, task_id: str) -> None:
    """Raise CancelledError when the hot record carries a cancel request."""
    record = await task_io.get_task(task_id)
    if record.get("cancel_requested"):
        raise asyncio.CancelledError()


async def cooperative_delay(task_io: TaskIO, task_id: str, seconds: float,
                            *, slice_seconds: float = 1.0) -> None:
    """Sleep in slices, honouring cancel requests between slices."""
    remaining = seconds
    while remaining > 0:
        await raise_if_cancelled(task_io, task_id)
        step = min(slice_seconds, remaining)
        await asyncio.sleep(step)
        remaining -= step