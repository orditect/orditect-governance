"""Interval topology over the dependency graph.

Edges are pure facts (child depends on parent). The interval
[start, end] contains exactly the nodes lying on a path from start to
end: descendants of start INTERSECT ancestors of end, plus both
endpoints. External dependencies of in-range nodes (not reachable from
start) are outside the interval and are never replayed — their inputs
come from the archive via the caller's build_task.
"""

from __future__ import annotations

from typing import Any, Iterable


def _maps(edges: Iterable) -> tuple[dict, dict]:
    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for e in edges:
        # Accept DependencyEdge objects (attribute access) and plain
        # dicts (subscript) — the graph store returns the former.
        if hasattr(e, "child_id"):
            child, parent = e.child_id, e.parent_id
        else:
            child, parent = e["child_id"], e["parent_id"]
        parents.setdefault(child, []).append(parent)
        children.setdefault(parent, []).append(child)
    return parents, children


def _reach(start: str, links: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(links.get(node, []))
    return seen


def subgraph_between(start_id: str, end_id: str,
                     edges: Iterable[dict]) -> set[str]:
    """Nodes on any path from start to end (inclusive)."""
    parents, children = _maps(edges)
    descendants = _reach(start_id, children)
    ancestors = _reach(end_id, parents)
    return (descendants & ancestors) | {start_id, end_id}


def topo_order(start_id: str, end_id: str,
               edges: Iterable[dict]) -> list[str]:
    """Execution order over the interval: dependencies before dependents.

    Deterministic: start first, then alphabetical among ready nodes.
    Raises ValueError when start cannot reach end (no such interval),
    on cycles, or on a disconnected interval.
    """
    edge_list = list(edges)
    included = subgraph_between(start_id, end_id, edge_list)

    if start_id != end_id:
        _, children = _maps(edge_list)
        if end_id not in _reach(start_id, children):
            raise ValueError(
                f"no path from {start_id} to {end_id}: "
                f"the interval does not exist"
            )

    parents_in: dict[str, list[str]] = {n: [] for n in included}
    children_of: dict[str, list[str]] = {}
    for e in edge_list:
        if hasattr(e, "child_id"):
            child, parent = e.child_id, e.parent_id
        else:
            child, parent = e["child_id"], e["parent_id"]
        if child in included and parent in included:
            parents_in[child].append(parent)
            children_of.setdefault(parent, []).append(child)

    def _rank(node: str) -> tuple[bool, str]:
        return (node != start_id, node)

    ready = sorted((n for n, ps in parents_in.items() if not ps), key=_rank)
    order: list[str] = []
    remaining = {n: list(ps) for n, ps in parents_in.items()}
    while ready:
        node = ready.pop(0)
        order.append(node)
        for child in sorted(children_of.get(node, [])):
            remaining[child].remove(node)
            if not remaining[child]:
                ready.append(child)
        ready.sort(key=_rank)
    if len(order) != len(included):
        raise ValueError(
            f"interval [{start_id}, {end_id}] is cyclic or disconnected"
        )
    return order