"""FanOutPattern: dynamic worker fan-out plus supervision.

Abstracted from the reference application's research supervisor. The
pattern submits one child per input item, writes the dynamic dependency
edges, waits for terminal states, and reports tallies. Retry/reuse
discipline (if_not_exists) makes a supervisor rerun reuse already
settled children instead of resubmitting them.

Business concerns stay in the callbacks: how to build a child's task
id from an item, and how to build the child's task instance. The
pattern never inspects item contents.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from ordigovernance.runtime.patterns.dynamic_edge_writer import (
    EdgeIO,
    edge_fact,
    write_edges,
)
from ordigovernance.runtime.task.governed_task import TaskIO


@dataclass(frozen=True)
class FanOutResult:
    """Terminal tallies plus the child records of one fan-out round."""

    child_ids: tuple[str, ...]
    records: dict[str, dict]
    succeeded: int
    failed: tuple[str, ...] = field(default_factory=tuple)
    cancelled: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.child_ids)


class FanOutPattern:
    """Submit children, write edges, supervise to terminal states."""

    def __init__(self, orchestrator: Any, task_io: TaskIO, *,
                 edge_io: EdgeIO | None = None,
                 step_timeout: float = 300.0) -> None:
        self._orchestrator = orchestrator
        self._task_io = task_io
        self._edge_io = edge_io
        self._step_timeout = step_timeout

    async def run(
            self,
            supervisor_id: str,
            items: Iterable[Any],
            *,
            build_child_id: Callable[..., str],
            build_child: Callable[..., str],
            if_not_exists: bool = True,
            parent_task_id: str | None = None,
    ) -> FanOutResult:
        """Fan out one child per item and wait for all terminal states.

        Items are OPAQUE child declarations: heterogeneous fan-outs put
        whatever the builders need (type, params, context) into each
        item. Both builders accept either (position, item[, child_id])
        — the legacy homogeneous shape — or (item[, child_id]); the
        callback's own signature decides. build_child_id returns the
        child task id, build_child the task instance.

        parent_task_id: the snapshot parent of every child. Inside an
        executing supervisor node this is injected by the executor's
        contextvar and must stay None; a drive-layer fan-out (no
        executing node) MUST pass it explicitly, or the children's
        snapshot parentage stays empty and sink actions that walk the
        tree (resume_tree / retry_scope) cannot find them.
        """
        id_params = len(inspect.signature(build_child_id).parameters)
        child_params = len(inspect.signature(build_child).parameters)

        def _make_id(position: int, item: Any) -> str:
            if id_params >= 2:
                return build_child_id(position, item)
            return build_child_id(item)

        def _make_child(position: int, item: Any, child_id: str) -> Any:
            if child_params >= 3:
                return build_child(position, item, child_id)
            if child_params == 2:
                return build_child(item, child_id)
            return build_child(item)

        child_ids: list[str] = []
        for position, item in enumerate(items, start=1):
            child_id = _make_id(position, item)
            await self._orchestrator.submit(
                _make_child(position, item, child_id),
                task_id=child_id,
                if_not_exists=if_not_exists,
                parent_task_id=parent_task_id,
            )
            child_ids.append(child_id)
            if self._edge_io is not None:
                await write_edges(self._edge_io, [
                    edge_fact(child_id, supervisor_id, is_primary=True),
                ])

        records_list = await asyncio.gather(*[
            self._orchestrator.wait_terminal(cid, timeout=self._step_timeout)
            for cid in child_ids
        ])
        records = dict(zip(child_ids, records_list))
        succeeded = sum(1 for r in records_list if r["status"] == "succeeded")
        failed = tuple(cid for cid, r in records.items()
                       if r["status"] == "failed")
        cancelled = tuple(cid for cid, r in records.items()
                          if r["status"] == "cancelled")
        return FanOutResult(
            child_ids=tuple(child_ids), records=records,
            succeeded=succeeded, failed=failed, cancelled=cancelled,
        )

    async def wait_resumed(
        self,
        supervisor_id: str,
        cancelled: Iterable[str],
        *,
        poll_interval: float = 0.5,
    ) -> None:
        """Block until every cancelled child has been externally resumed.

        HITL beat: paused children settle as cancelled; the supervisor
        blocks until an external resume reruns them to success. The
        supervisor's own cancel request still aborts the wait.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._step_timeout
        pending = list(cancelled)
        while pending:
            if loop.time() > deadline:
                raise TimeoutError(
                    f"children not resumed within timeout: {pending}"
                )
            await asyncio.sleep(poll_interval)
            rec_self = await self._task_io.get_task(supervisor_id)
            if rec_self.get("cancel_requested"):
                raise asyncio.CancelledError()
            still: list[str] = []
            for cid in pending:
                rec = await self._task_io.get_task(cid)
                if rec.get("status") != "succeeded":
                    still.append(cid)
            pending = still