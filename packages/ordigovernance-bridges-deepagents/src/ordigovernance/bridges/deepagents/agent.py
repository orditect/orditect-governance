
"""build_tracked_agent: assemble a deepagents agent over tracked atoms.

Status: SKELETON. The deepagents package API surface is not pinned by
this project's environment yet; the assembly contract IS pinned:

  1. The agent's LLM is a tracked chat model (LangChainTrackedLLM —
     deepagents speaks LangChain messages, the langgraph bridge shell
     is reused verbatim).
  2. The agent's tools are tracked (as_langchain_tools).
  3. The agent loop runs INSIDE one task interval; its iterations are
     governed at call level, its boundary at task level.

Until the deepagents dependency is installed and its factory signature
confirmed, this module exposes the wiring plan as code and raises at
construction time.
"""

from __future__ import annotations

from typing import Any

from ordigovernance.bridges.langgraph.tracked_llm import LangChainTrackedLLM
from ordigovernance.bridges.langgraph.tracked_tools import as_langchain_tools
from ordigovernance.api.atoms import TrackedLLMProtocol
from ordigovernance.api.atoms import TrackedToolSetProtocol


def build_tracked_agent(tracked_llm: TrackedLLMProtocol,
                        tracked_tools: TrackedToolSetProtocol,
                        tool_specs: dict[str, dict],
                        **agent_kwargs: Any) -> Any:
    """Assemble one deepagents agent over tracked atoms.

    tool_specs: {name: {"description": str, "args_schema": ...}} — the
    framework-facing tool facts; handlers live behind tracked_tools.
    """
    try:
        import deepagents  # noqa: F401
    except ImportError:
        raise ImportError(
            "bridges.deepagents requires the deepagents package: "
            "pip install ordigovernance-bridges-deepagents[deepagents]"
        ) from None

    model = LangChainTrackedLLM(tracked=tracked_llm)
    tools = as_langchain_tools(tracked_tools, tool_specs)
    # TODO: pin the deepagents factory call once its API is confirmed
    # against the installed version. The two tracked objects above are
    # the entire governance surface; everything else is framework
    # vocabulary.
    raise NotImplementedError(
        "deepagents factory wiring pending API confirmation; "
        "tracked model and tools are ready"
    )