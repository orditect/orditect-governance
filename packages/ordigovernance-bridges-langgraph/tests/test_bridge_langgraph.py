"""LangGraph bridge shell tests: format translation only, no runtime needed.

The bridge's governance comes entirely from the tracked atoms (covered
by test_bridge_contract.py); these tests pin the message/tool FORMAT
adaptation. Skipped when langchain-core is not installed.
"""

import asyncio

import pytest

lc = pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ordigovernance.bridges.langgraph.tracked_llm import (
    LangChainTrackedLLM,
    _to_ai_message,
    _to_dicts,
)
from ordigovernance.bridges.langgraph.tracked_tools import as_langchain_tools


from conftest import run

class FakeTrackedLLM:
    client_name = "research"

    def __init__(self):
        self.requests = []

    async def complete(self, messages, **kwargs):
        self.requests.append(messages)
        return {
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                      "total_tokens": 5},
        }


def test_to_dicts_maps_langchain_message_types():
    dicts = _to_dicts([
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ])
    assert dicts == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_to_ai_message_maps_response_and_usage():
    msg = _to_ai_message({
        "choices": [{"message": {"content": "answer"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                  "total_tokens": 5},
    })
    assert isinstance(msg, AIMessage)
    assert msg.content == "answer"
    assert msg.usage_metadata["total_tokens"] == 5


def test_chat_model_invocation_goes_through_tracked():
    tracked = FakeTrackedLLM()
    model = LangChainTrackedLLM(tracked=tracked)
    result = run(model.ainvoke([HumanMessage(content="hi")]))
    assert result.content == "answer"
    assert tracked.requests == [[{"role": "user", "content": "hi"}]]


def test_chat_model_sync_path_rejected():
    model = LangChainTrackedLLM(tracked=FakeTrackedLLM())
    with pytest.raises(NotImplementedError):
        model._generate([HumanMessage(content="hi")])


def test_as_langchain_tools_adapts_and_invokes():
    class FakeTrackedTools:
        def __init__(self):
            self.calls = []

        async def call(self, name, *args, inputs=None, **kwargs):
            self.calls.append((name, inputs))
            return {"ok": True}

    tracked = FakeTrackedTools()
    tools = as_langchain_tools(tracked, {
        "search": {"description": "web search"},
    })
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].description == "web search"

    result = run(tools[0].ainvoke({"kwargs": {"query": "ev"}}))
    assert result == {"ok": True}
    assert tracked.calls == [("search", {"query": "ev"})]

def test_as_langchain_tools_respects_explicit_schema():
    from pydantic import BaseModel

    class SearchArgs(BaseModel):
        query: str

    class FakeTrackedTools:
        def __init__(self):
            self.calls = []

        async def call(self, name, *args, inputs=None, **kwargs):
            self.calls.append((name, inputs))
            return {"ok": True}

    tracked = FakeTrackedTools()
    tools = as_langchain_tools(tracked, {
        "search": {"description": "web search", "args_schema": SearchArgs},
    })
    result = run(tools[0].ainvoke({"query": "ev"}))
    assert result == {"ok": True}
    assert tracked.calls == [("search", {"query": "ev"})]