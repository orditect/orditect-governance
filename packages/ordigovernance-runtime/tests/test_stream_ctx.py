"""ctx.llm_stream through the governed context (open-tier passthrough)."""

import pytest

from ordigovernance.runtime.agent.context import AgentContext
from ordigovernance.api.naming import SEQ_AGENT_BASE
from ordigovernance.api.task import GenerationMeta


class _FakeStreamLLM:
    def __init__(self):
        self.call_ids = []

    async def chat(self, messages, *, call_id, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}

    async def stream(self, messages, *, call_id, **kwargs):
        self.call_ids.append(call_id)
        for token in ("hel", "lo", " world"):
            yield {"delta": token}


class _ChatOnlyLLM:
    async def chat(self, messages, *, call_id, **kwargs):
        return {}


def _ctx(llms):
    meta = GenerationMeta(task_id="t", eid="e0001",
                          previous_eids=(), previous_status=None)
    return AgentContext(
        meta, tools=None, llms=llms,
        archive_backend=None, memo_scope="test",
    )


@pytest.mark.asyncio
async def test_ctx_llm_stream_yields_chunks_with_governed_call_id():
    llm = _FakeStreamLLM()
    ctx = _ctx({"publish": llm})
    chunks = [
        c["delta"]
        async for c in ctx.llm_stream("publish", "publish", 2,
                                      messages=[{"role": "user",
                                                 "content": "hi"}])
    ]
    assert chunks == ["hel", "lo", " world"]
    assert llm.call_ids == ["publish-t-e0001-2"]


@pytest.mark.asyncio
async def test_llm_stream_rejects_chat_only_clients():
    ctx = _ctx({"writing": _ChatOnlyLLM()})
    with pytest.raises(RuntimeError, match="does not expose stream"):
        async for _ in ctx.llm_stream("writing", "p", 1, messages=[]):
            pass