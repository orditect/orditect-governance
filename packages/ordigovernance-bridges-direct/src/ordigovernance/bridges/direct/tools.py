"""GovernedToolSet assembly from business-provided handlers."""

from __future__ import annotations

from ordigovernance.runtime.tools.governed_tools import GovernedToolSet


def build_tool_set(governor, budget, store, *, task_id: str,
                   tools: dict[str, dict],
                   memory_read=None, memory_write=None,
                   memory_resource: str = "llm_research",
                   memory_event_type: str = "memory_call") -> GovernedToolSet:
    """Register business handlers as governed tools.

    tools: {name: {"handler": fn, "resource": str, "event_type": str}}.
    """
    tool_set = GovernedToolSet(
        governor, budget, store, task_id=task_id,
        memory_read_handler=memory_read,
        memory_write_handler=memory_write,
        memory_resource=memory_resource,
        memory_event_type=memory_event_type,
    )
    for name, spec in tools.items():
        tool_set.register(name, spec["handler"],
                          resource=spec["resource"],
                          event_type=spec["event_type"],
                          side_effect=spec.get("side_effect", "readonly"))
    return tool_set