"""Passthrough tracked atoms: mechanism-direct A/B atoms.

Every call is a real governed call (semaphore / budget / audit /
pointer-ization all apply behind ctx), just without memo reuse or
replay policy routing. Locks the seq band discipline, the governed
call-id shape, the origin recording, and stream pass-through.
"""

from __future__ import annotations

import pytest

from ordigovernance.api.naming import SEQ_AGENT_BASE
from ordigovernance.api.task import GenerationMeta
from ordigovernance.runtime.agent.context import AgentContext
from ordigovernance.runtime.atoms import (
    PassthroughTrackedLLM,
    PassthroughTrackedToolSet,
)


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
        self.calls.append({"call_id": call_id, "kwargs": kwargs})
        return {"choices": [{"message": {"content": "ok"}}]}

    async def stream(self, messages, *, call_id, **kwargs):
        self.calls.append({"call_id": call_id, "stream": True})
        for token in ("a", "b"):
            yield {"delta": token}


def _ctx(tools=None, llm=None):
    meta = GenerationMeta(task_id="t", eid="e0001",
                          previous_eids=(), previous_status=None)
    return AgentContext(
        meta, tools=tools, llms={"research": llm} if llm else {},
        archive_backend=None, memo_scope="test",
    )


class TestStructuralProtocols:
    def test_llm_matches_protocol(self):
        from ordigovernance.api.atoms import TrackedLLMProtocol

        assert isinstance(PassthroughTrackedLLM(_ctx(), "research"),
                          TrackedLLMProtocol)

    def test_tools_match_protocol(self):
        from ordigovernance.api.atoms import TrackedToolSetProtocol

        assert isinstance(PassthroughTrackedToolSet(_ctx()),
                          TrackedToolSetProtocol)


class TestPassthroughTrackedLLM:
    @pytest.mark.asyncio
    async def test_complete_allocates_agent_band_seqs(self):
        llm = _FakeLLM()
        tracked = PassthroughTrackedLLM(_ctx(llm=llm), "research")
        await tracked.complete([{"role": "user", "content": "a"}])
        await tracked.complete([{"role": "user", "content": "b"}])
        assert [c["call_id"] for c in llm.calls] == [
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 1}",
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 2}",
        ]

    @pytest.mark.asyncio
    async def test_client_name_property(self):
        tracked = PassthroughTrackedLLM(_ctx(), "research")
        assert tracked.client_name == "research"

    @pytest.mark.asyncio
    async def test_stream_passes_through(self):
        llm = _FakeLLM()
        tracked = PassthroughTrackedLLM(_ctx(llm=llm), "research")
        chunks = [c async for c in tracked.stream(
            [{"role": "user", "content": "a"}])]
        assert chunks == [{"delta": "a"}, {"delta": "b"}]
        assert llm.calls[0]["call_id"] == \
            f"agent-llm-t-e0001-{SEQ_AGENT_BASE + 1}"


class TestPassthroughTrackedToolSet:
    @pytest.mark.asyncio
    async def test_call_lands_as_governed_call(self):
        tools = _FakeTools()
        ctx = _ctx(tools=tools)
        tracked = PassthroughTrackedToolSet(ctx)
        result = await tracked.call("search", inputs={"q": "ev"})
        assert tools.calls == [
            ("search", f"search-t-e0001-{SEQ_AGENT_BASE + 1}")]
        assert result["params"] == {"q": "ev"}

    @pytest.mark.asyncio
    async def test_origins_record_executed(self):
        ctx = _ctx(tools=_FakeTools())
        tracked = PassthroughTrackedToolSet(ctx)
        await tracked.call("search", inputs={"q": "ev"})
        assert ctx.origins["search"] == ["executed"]
        assert ctx.origin_of("search") == "executed"

    @pytest.mark.asyncio
    async def test_record_origin_opt_out(self):
        ctx = _ctx(tools=_FakeTools())
        tracked = PassthroughTrackedToolSet(ctx)
        await tracked.call("search", inputs={"q": "ev"},
                           record_origin=False)
        assert "search" not in ctx.origins