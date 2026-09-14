"""Task-level structural contracts (hot-record IO + generation identity)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskIO(Protocol):
    """Minimal hot-record IO the task/agent layer needs."""

    async def get_task(self, task_id: str) -> dict: ...

    async def update_task(self, task_id: str, patch: dict) -> None: ...


@dataclass(frozen=True)
class GenerationMeta:
    """Generation identity read from the hot record."""

    task_id: str
    eid: str
    previous_eids: tuple[str, ...]
    previous_status: str | None

    @property
    def tag(self) -> str:
        return f"[{self.task_id} ...{self.eid[-4:]}]"


__all__ = ["GenerationMeta", "TaskIO"]