"""build_tracked_agent: assemble a deepagents agent over tracked atoms.

Same shape as the LangGraph bridge (both ecosystems speak LangChain
messages): the model is a tracked chat model, business tools are
tracked, and the framework's agent loop runs INSIDE one task
interval — its iterations governed at call level, its boundary at
task level.

Governance disclosure: only model calls and the tools passed via
tool_specs flow through the governed plane. DeepAgents' internal
middleware vocabulary (planning notes, the virtual filesystem,
subagent spawning) executes ungoverned inside the interval; a
subagent whose model is itself a LangChainTrackedLLM shares the
governed model path.
"""

from __future__ import annotations

from typing import Any

from ordigovernance.api.atoms import TrackedLLMProtocol, TrackedToolSetProtocol
from ordigovernance.bridges.langgraph.tracked_llm import LangChainTrackedLLM
from ordigovernance.bridges.langgraph.tracked_tools import as_langchain_tools

try:
    from deepagents import create_deep_agent
except ImportError:  # pragma: no cover - exercised via monkeypatch
    create_deep_agent = None


def build_tracked_agent(tracked_llm: TrackedLLMProtocol,
                        tracked_tools: TrackedToolSetProtocol,
                        tool_specs: dict[str, dict],
                        **agent_kwargs: Any) -> Any:
    """Assemble one deepagents agent over tracked atoms.

    tool_specs: {name: {"description": str, "args_schema": ...}} — the
    framework-facing tool facts; handlers live behind tracked_tools.
    agent_kwargs (instructions, subagents, ...) pass through to
    create_deep_agent verbatim; the two tracked objects above are the
    entire governance surface.
    """
    if create_deep_agent is None:
        raise ImportError(
            "bridges.deepagents requires the deepagents package: "
            "pip install ordigovernance-bridges-deepagents[deepagents]"
        )
    model = LangChainTrackedLLM(tracked=tracked_llm)
    tools = as_langchain_tools(tracked_tools, tool_specs)
    return create_deep_agent(model=model, tools=tools, **agent_kwargs)