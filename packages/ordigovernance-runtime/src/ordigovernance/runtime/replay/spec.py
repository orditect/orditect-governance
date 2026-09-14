"""Replay specifications and reports.

D1: replay input has exactly two sources — "archive" (pin a historical
generation's archived result; variance measurement) or "explicit"
(caller-given payload; behavior probe). The resolved pinned_input is an
opaque dict; the business impl interprets its keys (D5).

Archive resolution pins the ENTIRE archived result dict: the component
layer never selects business fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ordigovernance.api.memo import MemoBackend
from ordigovernance.api.side_effect import ReusePolicy
from ordigovernance.runtime.archive.archive import load_generation


@dataclass(frozen=True)
class ReplayInput:
    """Where a replay's pinned input comes from."""

    source: str  # "archive" | "explicit"
    pin_from: tuple[str, str] | None = None  # (task_id, eid)
    explicit: dict | None = None

    @classmethod
    def from_archive(cls, task_id: str, eid: str) -> "ReplayInput":
        return cls(source="archive", pin_from=(task_id, eid))

    @classmethod
    def from_explicit(cls, payload: dict) -> "ReplayInput":
        return cls(source="explicit", explicit=dict(payload))

    async def resolve(self, backend: MemoBackend | None, *,
                      reader_task_id: str = "replay",
                      reader_eid: str = "replay") -> dict:
        """Resolve to the pinned_input dict (opaque business payload)."""
        if self.source == "explicit":
            return dict(self.explicit or {})
        if self.pin_from is None:
            raise ValueError("archive source requires pin_from")
        task_id, eid = self.pin_from
        doc = await load_generation(
            backend, task_id=task_id, eid=eid,
            reader_task_id=reader_task_id, reader_eid=reader_eid,
        )
        if doc is None:
            raise KeyError(f"no archived generation for {task_id}@{eid}")
        return dict(doc.get("result") or {})


@dataclass(frozen=True)
class ReplaySpec:
    """Single-node replay request (experiment declaration)."""

    task_id: str
    input: ReplayInput
    times: int = 1
    memo_policy: ReusePolicy | None = None
    side_effect_policy: dict | None = None
    llm_params: dict | None = None
    label: str | None = None
    isolate_policy: bool = False


@dataclass(frozen=True)
class ReplayReport:
    """One replayed node's generation evidence."""

    task_id: str
    local_id: str
    generations: tuple[str, ...]
    statuses: tuple[str, ...]
    final_record: dict = field(default_factory=dict)
    drift: Any = None


@dataclass(frozen=True)
class ReplayRangeReport:
    """Interval replay evidence: one report tuple per round."""

    start_id: str
    end_id: str
    order: tuple[str, ...]
    rounds: tuple[tuple[ReplayReport, ...], ...]
    drift: Any = None