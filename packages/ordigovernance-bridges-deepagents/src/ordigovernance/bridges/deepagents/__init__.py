"""DeepAgents bridge: thin shells over the tracked atoms.

Same shape as the LangGraph bridge (both ecosystems speak LangChain
messages): LLM and tools are adapted to tracked atoms; the framework's
agent loop runs inside the task interval as a black box.

    from ordigovernance.bridges.deepagents import build_tracked_agent
"""

from ordigovernance.bridges.deepagents.agent import build_tracked_agent

__all__ = ["build_tracked_agent"]
