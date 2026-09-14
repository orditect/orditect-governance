"""Dynamic dependency edge writing (pure-edge facts, T12).

Nodes that fan out dynamically at runtime write one edge per spawned
child so the dependency graph reflects the run's actual shape. Edges
are DependencyEdge objects (the dependency store's write_dependency
contract); this module is a thin convenience wrapper, orditect-dependent.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable

from orditect.protocol import DependencyEdge


@runtime_checkable
class EdgeIO(Protocol):
    """Dependency-store write surface (LocalFileStore.dependency)."""

    async def write_dependency(self, edge: DependencyEdge) -> None: ...


def edge_fact(child_id: str, parent_id: str, *,
              is_primary: bool = True) -> DependencyEdge:
    """One pure-edge fact in the store's expected shape."""
    return DependencyEdge(child_id=child_id, parent_id=parent_id,
                          is_primary=is_primary)


async def write_edges(edge_io: EdgeIO, edges: Iterable[DependencyEdge]) -> None:
    """Persist a batch of edge facts (used by fan-out supervisors)."""
    for edge in edges:
        await edge_io.write_dependency(edge)