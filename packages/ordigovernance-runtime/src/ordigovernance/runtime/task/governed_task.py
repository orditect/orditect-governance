"""GovernedTask: the task-level governance base.

A governed task is a producing/reviewing/output unit consuming upstream
task artifacts (already archived). The base inhales the generation
governance that every producing task shares:

  - generation identity and previous-generation metadata from the hot
    record;
  - archive of every generation's result + lineage pins;
  - opaque pinned-input injection for the replay channel.

Business logic lives entirely in execute(); the base never orchestrates
upstream reads, prompts, or call sequences. Iteration policy (quality
gates, retry loops) is NOT here either: it is run policy and belongs to
the driver/patterns layer.

GenerationMeta and TaskIO are defined in ordigovernance-api (the contract
layer) and re-exported here for import convenience.
"""

from __future__ import annotations

import logging
from typing import Any

from ordigovernance.api.memo import MemoBackend
from ordigovernance.api.pinned import PinnedInputMixin
from ordigovernance.api.task import GenerationMeta, TaskIO
from ordigovernance.runtime.archive.archive import archive_generation

log = logging.getLogger(__name__)


class GovernedTask(PinnedInputMixin):
    """Base for governed producing tasks.

    Cooperative multiple inheritance: passes *args/**kwargs through, so
    it composes with orditect.flow.BaseBackEndTask or any other base the
    bridge stacks underneath.
    """

    def __init__(self, *args: Any, task_io: TaskIO,
                 archive_backend: MemoBackend | None = None,
                 pinned_input: dict | None = None, **kwargs: Any) -> None:
        super().__init__(*args, pinned_input=pinned_input, **kwargs)
        self._task_io = task_io
        self._archive_backend = archive_backend

    @property
    def archive_backend(self) -> MemoBackend | None:
        return self._archive_backend

    async def generation_meta(self, task_id: str) -> GenerationMeta:
        """Read the current generation identity from the hot record."""
        record = await self._task_io.get_task(task_id)
        return GenerationMeta(
            task_id=task_id,
            eid=record.get("execution_id", ""),
            previous_eids=tuple(record.get("previous_execution_ids", [])),
            previous_status=record.get("previous_status"),
        )

    async def archive(self, meta: GenerationMeta, result: dict,
                      pins: dict[str, str]) -> None:
        """Persist this generation's result + lineage pins."""
        await archive_generation(
            self._archive_backend, task_id=meta.task_id, eid=meta.eid,
            result=result, pins=pins,
        )


__all__ = ["GenerationMeta", "GovernedTask", "TaskIO"]