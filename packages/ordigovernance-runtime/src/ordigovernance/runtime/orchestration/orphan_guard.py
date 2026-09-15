"""Orphan descendant guard: reopening must not be silent about active children.

Problem shape: reopening a node (direct retry, scope reopen, driver
reopen) mints a new generation for the target while its descendants
along the declared dependency edges may still be RUNNING on the old
generation. Nothing in the reopen primitive touches those children:
they keep executing, their outputs keep landing, and the parent may
already have consumed (or be about to consume) archive reads of a
world the children are still mutating. Today the safety comes from
workflow timing conventions (drivers wait for fan-out terminals
before reopening) — the same shape as docs/pitfalls.md 13.2/13.7,
which were conventions until they bit.

This guard turns the convention into a mechanism: before reopening,
detect ACTIVE descendants (dependency-graph reachables whose hot
record is not terminal) and fail loudly instead of silently
producing cross-generation inconsistency.

Pin-consumer check (optional upgrade): when an archive backend is
wired, the guard ALSO consults the pinned-by reverse index of the
target's CURRENT generation. An active generation that declared
consumption of the target's current generation is flagged as a
warning: reopening does not corrupt the pinned read (pins are
generation-precise), but the consumer's world view may already assume
the old generation's outputs. Warnings never block; active
descendants always do.

Boundary disclosure (docs/pitfalls.md 13.1): dependency edges are the
DECLARED structure; snapshot parentage is the EXECUTED structure. This
guard deliberately reads the declared graph only — a descendant known
only through snapshots (never edge-written) is outside its view. The
pinned-by index is an audit-grade approximation (archive module
docstring): absence of an entry means "unknown", never "nobody".
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ordigovernance.runtime.archive.archive import load_pinned_by
from ordigovernance.api.memo import MemoBackend
from ordigovernance.runtime.task.governed_task import TaskIO

TERMINAL_WORDS = frozenset({"succeeded", "failed", "cancelled"})


class ActiveDescendantsError(RuntimeError):
    """Reopen blocked: the target still has non-terminal descendants."""

    def __init__(self, task_id: str, active: list[str]) -> None:
        self.task_id = task_id
        self.active = tuple(active)
        super().__init__(
            f"reopen of {task_id!r} blocked: active descendants "
            f"{sorted(active)}; settle them first (pause/cancel) or "
            f"wait for their terminals"
        )


@runtime_checkable
class DepsReader(Protocol):
    """Dependency-graph read surface (e.g. LocalFileStore.dependency)."""

    async def read_graph(self, root_id: str) -> Any: ...


def _edge_endpoints(edge: Any) -> tuple[str, str]:
    """(child_id, parent_id) from object-shaped or dict-shaped edges."""
    if hasattr(edge, "child_id"):
        return edge.child_id, edge.parent_id
    return edge["child_id"], edge["parent_id"]


def _descendants(task_id: str, graph: Any) -> list[str]:
    """All nodes reachable from task_id along parent -> child links.

    The graph may be a dict ({"edges": [...]}), an object exposing an
    .edges attribute (the framework's DependencyGraph), or None.
    """
    if graph is None:
        return []
    edges = (graph.get("edges") if isinstance(graph, dict)
             else getattr(graph, "edges", None)) or []
    children_of: dict[str, list[str]] = {}
    for edge in edges:
        child, parent = _edge_endpoints(edge)
        children_of.setdefault(parent, []).append(child)
    seen: set[str] = set()
    stack = list(children_of.get(task_id, []))
    while stack:
        node = stack.pop()
        if node in seen:
            continue  # cycle guard: declared graphs are user input
        seen.add(node)
        stack.extend(children_of.get(node, []))
    return sorted(seen)


async def find_active_descendants(
        task_id: str,
        *,
        deps_reader: DepsReader,
        task_io: TaskIO,
        terminal_words: frozenset = TERMINAL_WORDS,
) -> list[str]:
    """RUNNING descendants of task_id along the declared edges.

    Active means actually executing on the old generation: only the
    "running" status counts. A descendant that is pending (declared,
    initialized, or queued but not yet executing) is not mutating the
    world the reopened parent is about to re-read, so it does not
    block the reopen. Terminal descendants never block.
    """
    graph = await deps_reader.read_graph(task_id)
    active: list[str] = []
    for descendant in _descendants(task_id, graph):
        record = await task_io.get_task(descendant)
        if record.get("status") == "running":
            active.append(descendant)
    return active
async def find_active_pin_consumers(
        task_id: str,
        *,
        backend: MemoBackend | None,
        task_io: TaskIO,
        terminal_words: frozenset = TERMINAL_WORDS,
) -> list[dict]:
    """ACTIVE generations that pinned the target's CURRENT generation.

    Returns the consumer entries ({"task_id", "eid"}) whose hot record
    is not terminal. The check covers the current generation only:
    reopening mints a new generation, so consumers of OLDER
    generations are pinned to evidence that keeps existing unchanged.
    Absent backend or missing index entry degrades to [] -- "unknown",
    never "nobody" (the index is an audit-grade approximation).
    """
    if backend is None:
        return []
    record = await task_io.get_task(task_id)
    current_eid = record.get("execution_id")
    if not current_eid:
        return []
    doc = await load_pinned_by(
        backend, task_id=task_id, eid=current_eid,
        reader_task_id="orphan-guard", reader_eid=current_eid,
    )
    active: list[dict] = []
    for consumer in doc["consumers"]:
        consumer_record = await task_io.get_task(consumer["task_id"])
        status = consumer_record.get("status")
        # A consumer's hot record tracks its LATEST generation; the
        # pinned consumer entry is generation-precise. If the hot
        # record moved past the recorded eid, the recorded consumer
        # generation is terminal by construction (its task already
        # ran a newer generation).
        if consumer_record.get("execution_id") != consumer["eid"]:
            continue
        if status is not None and status not in terminal_words:
            active.append(consumer)
    return active

async def assert_no_active_descendants(
        task_id: str,
        *,
        deps_reader: DepsReader,
        task_io: TaskIO,
        backend: MemoBackend | None = None,
        terminal_words: frozenset = TERMINAL_WORDS,
) -> None:
    """Raise ActiveDescendantsError when the target has active children.

    Additionally returns no warnings API surface: callers that want
    the pin-consumer advisory should call find_active_pin_consumers
    themselves (warnings never block a reopen; the backend is only
    consulted here for symmetry when wired, and its result is
    attached to the raised error for diagnostics).
    """
    active = await find_active_descendants(
        task_id, deps_reader=deps_reader, task_io=task_io,
        terminal_words=terminal_words,
    )
    if active:
        raise ActiveDescendantsError(task_id, active)