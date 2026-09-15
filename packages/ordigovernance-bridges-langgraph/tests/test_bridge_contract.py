"""Bridge contract suite: LangChain shells over the runtime's tracked atoms.

Every invocation crossing the shell boundary must land as a governed
call with the naming-discipline identity (per-purpose call id, agent
band seq) and record its origin on the context. Requires langchain-core
plus the open runtime; the full react-graph test additionally needs
langgraph (skipped otherwise).
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ordigovernance.api.naming import SEQ_AGENT_BASE
from ordigovernance.api.task import GenerationMeta
from ordigovernance.bridges.langgraph import (
    LangChainTrackedLLM,
    as_langchain_tools,
    build_react_agent,
)
from ordigovernance.bridges.langgraph import react_agent as react_agent_mod
from ordigovernance.bridges.langgraph.tracked_llm import (
    _to_ai_message,
    _to_dicts,
)
from ordigovernance.runtime.agent.context import AgentContext
from ordigovernance.runtime.atoms import (
    PassthroughTrackedLLM,
    PassthroughTrackedToolSet,
)

from conftest import run


class _FakeTools:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def call(self, name, *args, call_id, params=None, **kwargs):
        self.calls.append((name, call_id))
        return {"tool": name, "call_id": call_id, "params": params}


class _FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []

    async def chat(self, messages, *, call_id, **kwargs):
        self.calls.append({"call_id": call_id, "messages": messages,
                           "kwargs": kwargs})
        return {"choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}


class _ToolCallingLLM:
    """First chat requests one OpenAI-shaped tool call, then answers."""

    def __init__(self):
        self.calls: list[dict] = []

    async def chat(self, messages, *, call_id, **kwargs):
        self.calls.append({"call_id": call_id, "messages": messages,
                           "kwargs": kwargs})
        if len(self.calls) == 1:
            return {"choices": [{"message": {
                "content": "",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "search",
                                 "arguments": '{"query": "ev"}'}}]}}]}
        return {"choices": [{"message": {"content": "final answer"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1,
                          "total_tokens": 3}}


def _ctx(tools=None, llm=None):
    meta = GenerationMeta(task_id="t", eid="e0001",
                          previous_eids=(), previous_status=None)
    return AgentContext(
        meta, tools=tools, llms={"research": llm} if llm else {},
        archive_backend=None, memo_scope="test",
    )


class TestChatShellContract:
    def test_invocation_lands_as_governed_call(self):
        llm = _FakeLLM()
        ctx = _ctx(llm=llm)
        model = LangChainTrackedLLM(
            tracked=PassthroughTrackedLLM(ctx, "research"))
        result = run(model.ainvoke([HumanMessage(content="hi")]))
        assert result.content == "ok"
        assert [c["call_id"] for c in llm.calls] == [
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 1}"]
        assert llm.calls[0]["messages"] == [{"role": "user", "content": "hi"}]

    def test_sequential_invocations_allocate_agent_band(self):
        llm = _FakeLLM()
        ctx = _ctx(llm=llm)
        model = LangChainTrackedLLM(
            tracked=PassthroughTrackedLLM(ctx, "research"))
        run(model.ainvoke([HumanMessage(content="a")]))
        run(model.ainvoke([HumanMessage(content="b")]))
        assert [c["call_id"] for c in llm.calls] == [
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 1}",
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 2}"]


class TestToolShellContract:
    def test_tool_call_lands_as_governed_call_with_origin(self):
        tools = _FakeTools()
        ctx = _ctx(tools=tools)
        lc_tools = as_langchain_tools(
            PassthroughTrackedToolSet(ctx),
            {"search": {"description": "web search"}})
        result = run(lc_tools[0].ainvoke({"kwargs": {"query": "ev"}}))
        assert tools.calls == [
            ("search", f"search-t-e0001-{SEQ_AGENT_BASE + 1}")]
        assert result["params"] == {"query": "ev"}
        assert ctx.origins["search"] == ["executed"]

    def test_tool_node_shaped_direct_args(self):
        """ToolNode passes the model's args dict straight in — the
        passthrough schema merges it identically to the nested form."""
        tools = _FakeTools()
        ctx = _ctx(tools=tools)
        lc_tools = as_langchain_tools(
            PassthroughTrackedToolSet(ctx),
            {"search": {"description": "web search"}})
        result = run(lc_tools[0].ainvoke({"query": "ev"}))
        assert tools.calls == [
            ("search", f"search-t-e0001-{SEQ_AGENT_BASE + 1}")]
        assert result["params"] == {"query": "ev"}


class TestWireShape:
    def test_assistant_tool_calls_round_trip_to_openai(self):
        dicts = _to_dicts([
            AIMessage(content="", tool_calls=[{
                "name": "search", "args": {"query": "ev"}, "id": "call_1"}]),
            ToolMessage(content='{"hits": 3}', tool_call_id="call_1"),
        ])
        assert dicts[0]["tool_calls"] == [{
            "id": "call_1", "type": "function",
            "function": {"name": "search", "arguments": '{"query": "ev"}'}}]
        assert dicts[1] == {"role": "tool", "content": '{"hits": 3}',
                            "tool_call_id": "call_1"}

    def test_openai_tool_calls_translate_to_toolcall_dicts(self):
        msg = _to_ai_message({"choices": [{"message": {
            "content": "",
            "tool_calls": [{
                "id": "call_9", "type": "function",
                "function": {"name": "search",
                             "arguments": '{"query": "ev"}'}}]}}]})
        assert msg.tool_calls == [
            {"name": "search", "args": {"query": "ev"}, "id": "call_9",
             "type": "tool_call"}]


class TestBindTools:
    def test_bind_tools_forwards_openai_specs_on_every_call(self):
        class _RecordingTracked:
            client_name = "research"

            def __init__(self):
                self.kwargs = None

            async def complete(self, messages, **kwargs):
                self.kwargs = kwargs
                return {"choices": [{"message": {"content": "ok"}}]}

        tracked = _RecordingTracked()
        model = LangChainTrackedLLM(tracked=tracked)
        bound = model.bind_tools(as_langchain_tools(
            PassthroughTrackedToolSet(_ctx(tools=_FakeTools())),
            {"search": {"description": "web search"}}))
        run(bound.ainvoke([HumanMessage(content="hi")]))
        specs = tracked.kwargs["tools"]
        assert specs[0]["function"]["name"] == "search"
        assert model._openai_tools is None  # clone discipline


class TestReactAgentAssembly:
    def test_factory_receives_tracked_model_and_tools(self, monkeypatch):
        captured = {}

        def fake_create(model, tools, **kwargs):
            captured.update(model=model, tools=tools, kwargs=kwargs)
            return "compiled-graph"

        monkeypatch.setattr(react_agent_mod, "create_react_agent",
                            fake_create)
        tracked_llm = PassthroughTrackedLLM(_ctx(), "research")
        agent = build_react_agent(
            tracked_llm, PassthroughTrackedToolSet(_ctx()),
            {"search": {"description": "web search"}},
            prompt="You are helpful.")
        assert agent == "compiled-graph"
        assert isinstance(captured["model"], LangChainTrackedLLM)
        assert captured["model"].tracked is tracked_llm
        assert [t.name for t in captured["tools"]] == ["search"]
        assert captured["kwargs"] == {"prompt": "You are helpful."}

    def test_missing_langgraph_raises_clearly(self, monkeypatch):
        monkeypatch.setattr(react_agent_mod, "create_react_agent", None)
        with pytest.raises(ImportError, match="langgraph"):
            build_react_agent(PassthroughTrackedLLM(_ctx(), "research"),
                              PassthroughTrackedToolSet(_ctx()), {})


class TestReactAgentFullLoop:
    def test_tool_loop_every_call_governed(self):
        pytest.importorskip("langgraph")
        tools = _FakeTools()
        llm = _ToolCallingLLM()
        ctx = _ctx(tools=tools, llm=llm)
        agent = build_react_agent(
            PassthroughTrackedLLM(ctx, "research"),
            PassthroughTrackedToolSet(ctx),
            {"search": {"description": "web search"}},
        )
        state = run(agent.ainvoke({"messages": [("user", "research ev")]}))
        assert state["messages"][-1].content == "final answer"
        # Both model iterations and the tool execution are governed
        # calls inside the agent band, under THIS generation's eid.
        assert [c["call_id"] for c in llm.calls] == [
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 1}",
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 2}"]
        assert tools.calls == [
            ("search", f"search-t-e0001-{SEQ_AGENT_BASE + 1}")]
        assert ctx.origins["search"] == ["executed"]
        # The tool round-trip reached the second model call in wire shape.
        second = llm.calls[1]["messages"]
        assert second[-1]["role"] == "tool"
        assert second[-1]["tool_call_id"] == "call_1"
        assert second[-2]["tool_calls"][0]["function"]["name"] == "search"