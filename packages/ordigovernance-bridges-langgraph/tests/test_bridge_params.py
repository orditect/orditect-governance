"""Regression locks for the bridge parameter-transport fixes.

Covers: stop/bind-kwargs forwarding through LangChainTrackedLLM, the
additional_kwargs tool_calls fallback, stream-chunk text extraction,
token-level _astream, and the reserved payload-key defense on the
runtime's passthrough tool atom.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage

from ordigovernance.api.task import GenerationMeta
from ordigovernance.bridges.langgraph.tracked_llm import (
    LangChainTrackedLLM,
    _chunk_text,
    _to_dicts,
)
from ordigovernance.runtime.agent.context import AgentContext
from ordigovernance.runtime.atoms import PassthroughTrackedToolSet

from conftest import run


class _RecordingTracked:
    client_name = "research"

    def __init__(self):
        self.kwargs = None

    async def complete(self, messages, **kwargs):
        self.kwargs = kwargs
        return {"choices": [{"message": {"content": "ok"}}]}

    async def stream(self, messages, **kwargs):
        self.kwargs = kwargs
        yield {"delta": "hel"}
        yield {"choices": [{"delta": {"content": "lo"}}]}


def test_stop_and_bind_kwargs_forwarded():
    tracked = _RecordingTracked()
    model = LangChainTrackedLLM(tracked=tracked)
    bound = model.bind_tools([], parallel_tool_calls=False)
    run(bound.ainvoke([HumanMessage(content="hi")], stop=["###"]))
    assert tracked.kwargs["stop"] == ["###"]
    assert tracked.kwargs["tools"] == []
    assert tracked.kwargs["parallel_tool_calls"] is False
    # Clone discipline: the unbound model never carries bind options.
    assert model._openai_tools is None
    assert model._bind_kwargs is None


def test_additional_kwargs_tool_calls_pass_through():
    raw = [{"id": "call_1", "type": "function",
            "function": {"name": "search", "arguments": "{}"}}]
    msg = AIMessage(content="")
    msg.additional_kwargs["tool_calls"] = raw
    dicts = _to_dicts([msg])
    assert dicts[0]["tool_calls"] == raw


def test_chunk_text_shapes():
    assert _chunk_text({"delta": "ab"}) == "ab"
    assert _chunk_text({"choices": [{"delta": {"content": "cd"}}]}) == "cd"
    assert _chunk_text({"choices": [{"delta": {}}]}) == ""
    assert _chunk_text({"delta": {"content": "ef"}}) == "ef"


def test_astream_yields_text_chunks():
    tracked = _RecordingTracked()
    model = LangChainTrackedLLM(tracked=tracked)

    async def collect():
        return [c async for c in
                model.astream([HumanMessage(content="hi")])]

    chunks = run(collect())
    assert "".join(c.content for c in chunks) == "hello"


def test_reserved_payload_keys_fail_loudly():
    meta = GenerationMeta(task_id="t", eid="e0001",
                          previous_eids=(), previous_status=None)
    ctx = AgentContext(meta, tools=None, llms={},
                       archive_backend=None, memo_scope="test")
    tracked = PassthroughTrackedToolSet(ctx)
    with pytest.raises(ValueError, match="collide"):
        run(tracked.call("search", inputs={}, params={"q": "x"}))