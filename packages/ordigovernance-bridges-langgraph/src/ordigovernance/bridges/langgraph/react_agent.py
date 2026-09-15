"""build_react_agent: assemble a LangGraph react agent over tracked atoms.

The bridge's full assembly: every model invocation the graph schedules
(including its internal tool-loop iterations) lands as one governed
call on the tracked LLM, and every tool execution lands as one
governed call on the tracked tool set. The graph runs INSIDE one task
interval: call-level governance applies to its iterations, task-level
governance (generations, archive, pins) to the enclosing node.

Canonical usage inside an impl's run(ctx):

    agent = build_react_agent(
        PassthroughTrackedLLM(ctx, "research"),
        PassthroughTrackedToolSet(ctx),
        {"search": {"description": "web search"}},
    )
    state = await agent.ainvoke({"messages": [("user", task)]})

Engine tiers inject their own tracked atoms behind the same
protocols; this function never changes across tiers.
"""

from __future__ import annotations

from typing import Any

from ordigovernance.api.atoms import TrackedLLMProtocol, TrackedToolSetProtocol
from ordigovernance.bridges.langgraph.tracked_llm import LangChainTrackedLLM
from ordigovernance.bridges.langgraph.tracked_tools import as_langchain_tools

try:
    # LangGraph V1.0+: the react prebuilt moved into langchain.agents.
    from langchain.agents import create_agent as create_react_agent
except ImportError:
    try:  # optional dependency: the legacy home of the react prebuilt
        from langgraph.prebuilt import create_react_agent
    except ImportError:  # pragma: no cover - exercised via monkeypatch
        create_react_agent = None

def build_react_agent(tracked_llm: TrackedLLMProtocol,
                      tracked_tools: TrackedToolSetProtocol,
                      tool_specs: dict[str, dict],
                      **agent_kwargs: Any) -> Any:
    """Assemble one LangGraph react agent over tracked atoms.

    tracked_llm / tracked_tools are per-generation atoms (the caller
    binds them from the generation's AgentContext); tool_specs carries
    the framework-facing tool facts; agent_kwargs pass through to the
    installed factory verbatim (prompt, name, pre_model_hook, ...).

    Version drift note: on LangGraph V1+ the factory is
    langchain.agents.create_agent, which names the system-prompt
    parameter system_prompt (the legacy langgraph.prebuilt factory
    calls it prompt). Pass the name your installed version declares;
    the contract suite's monkeypatched factory pins the passthrough,
    and a real signature mismatch surfaces at assembly time.
    """
    if create_react_agent is None:
        raise ImportError(
            "build_react_agent requires the langgraph package: "
            "pip install ordigovernance-bridges-langgraph[langgraph]"
        )
    model = LangChainTrackedLLM(tracked=tracked_llm)
    tools = as_langchain_tools(tracked_tools, tool_specs)
    return create_react_agent(model, tools, **agent_kwargs)