"""AgentContext public read surface for engine components.

Engine components (nested intervals, engine tracked atoms) consume
only this public surface — never the context's private fields. The
surface is the open tier's contract with the engine tier: memo scope,
backend, registry views, and the engine-presence signal with its
guidance-raising accessor.
"""

from __future__ import annotations

import pytest

from ordigovernance.api.task import GenerationMeta
from ordigovernance.runtime.agent.context import AgentContext


class _FakeBackend:
    def __init__(self):
        self.store = {}

    async def memory_read(self, key, *, call_id, payload_fn=None):
        return {"key": key, "value": self.store.get(key)}

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.store[key] = value
        return {"key": key, "stored": True}


class _FakeMemoLayer:
    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        return await execute(), "executed"


class _FakeLLM:
    async def chat(self, messages, *, call_id, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}


def _ctx(*, memo_layer=None):
    meta = GenerationMeta(task_id="t", eid="e0001",
                          previous_eids=(), previous_status="cancelled")
    return AgentContext(
        meta, tools=None, llms={"research": _FakeLLM()},
        archive_backend=_FakeBackend(), memo_scope="run-scope",
        memo_layer=memo_layer,
    )


def test_memo_scope_is_public():
    assert _ctx().memo_scope == "run-scope"


def test_memo_backend_is_the_archive_backend():
    ctx = _ctx()
    assert ctx.memo_backend is not None


def test_generation_identity_is_public_via_meta():
    ctx = _ctx()
    assert ctx.meta.task_id == "t"
    assert ctx.meta.eid == "e0001"
    assert ctx.meta.previous_status == "cancelled"


def test_llm_registry_returns_a_copy():
    ctx = _ctx()
    registry = ctx.llm_registry
    assert sorted(registry) == ["research"]
    registry["injected"] = _FakeLLM()
    # Mutating the returned view never affects the context.
    assert sorted(ctx.llm_registry) == ["research"]


def test_has_memo_layer_tracks_injection():
    assert _ctx().has_memo_layer is False
    assert _ctx(memo_layer=_FakeMemoLayer()).has_memo_layer is True


def test_require_memo_layer_raises_with_guidance():
    with pytest.raises(RuntimeError, match="memo_layer_factory"):
        _ctx().require_memo_layer("nested intervals")


def test_require_memo_layer_returns_the_layer():
    layer = _FakeMemoLayer()
    assert _ctx(memo_layer=layer).require_memo_layer("x") is layer