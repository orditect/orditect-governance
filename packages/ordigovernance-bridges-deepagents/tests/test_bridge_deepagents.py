"""DeepAgents bridge assembly contract.

The bridge's entire governance surface is the two tracked objects:
the factory call is pinned with a fake create_deep_agent injected via
monkeypatch, so the contract is locked WITHOUT the heavy dependency.
A real install additionally runs the factory smoke at the bottom —
that is where a deepagents API drift would surface, and the only fix
point is build_tracked_agent.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from ordigovernance.bridges.deepagents import agent as agent_mod
from ordigovernance.bridges.deepagents import build_tracked_agent
from ordigovernance.bridges.langgraph.tracked_llm import LangChainTrackedLLM


class FakeTrackedLLM:
    client_name = "research"

    def __init__(self):
        self.requests = []

    async def complete(self, messages, **kwargs):
        self.requests.append(messages)
        return {"choices": [{"message": {"content": "answer"}}]}


class FakeTrackedTools:
    def __init__(self):
        self.calls = []

    async def call(self, name, *args, inputs=None, **kwargs):
        self.calls.append((name, inputs))
        return {"ok": True}


def test_factory_receives_tracked_model_and_tools(monkeypatch):
    captured = {}

    def fake_create(model=None, tools=None, **kwargs):
        captured.update(model=model, tools=tools, kwargs=kwargs)
        return "deep-agent"

    monkeypatch.setattr(agent_mod, "create_deep_agent", fake_create)
    tracked_llm = FakeTrackedLLM()
    result = build_tracked_agent(
        tracked_llm, FakeTrackedTools(),
        {"search": {"description": "web search"}},
        instructions="Research carefully.")
    assert result == "deep-agent"
    assert isinstance(captured["model"], LangChainTrackedLLM)
    assert captured["model"].tracked is tracked_llm
    assert [t.name for t in captured["tools"]] == ["search"]
    assert captured["kwargs"] == {"instructions": "Research carefully."}


def test_agent_kwargs_pass_through_verbatim(monkeypatch):
    captured = {}

    def fake_create(model=None, tools=None, **kwargs):
        captured.update(kwargs=kwargs)
        return "deep-agent"

    monkeypatch.setattr(agent_mod, "create_deep_agent", fake_create)
    build_tracked_agent(
        FakeTrackedLLM(), FakeTrackedTools(), {},
        subagents=[{"name": "helper", "description": "d",
                    "instructions": "..."}])
    assert captured["kwargs"]["subagents"][0]["name"] == "helper"


def test_missing_deepagents_raises_clearly(monkeypatch):
    monkeypatch.setattr(agent_mod, "create_deep_agent", None)
    with pytest.raises(ImportError, match="deepagents"):
        build_tracked_agent(FakeTrackedLLM(), FakeTrackedTools(), {})


def test_real_factory_signature_smoke():
    """With deepagents installed, the assembly must build a real graph."""
    pytest.importorskip("deepagents")
    agent = build_tracked_agent(
        FakeTrackedLLM(), FakeTrackedTools(),
        {"search": {"description": "web search"}},
        instructions="Research carefully.")
    assert agent is not None