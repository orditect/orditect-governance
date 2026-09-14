"""AgentContext.memoize on the open tier (no memo layer injected).

Locks the passthrough semantics: calls always execute, origins record
"executed", the stub mode is unreachable via the default resolver,
and record_stub_audit degrades silently without a recorder surface.
"""

from __future__ import annotations

import pytest

from ordigovernance.api.side_effect import CallClass
from ordigovernance.api.task import GenerationMeta
from ordigovernance.runtime.agent.context import AgentContext


def _ctx():
    meta = GenerationMeta(task_id="t", eid="e0001",
                          previous_eids=(), previous_status=None)
    return AgentContext(
        meta, tools=None, llms={},
        archive_backend=None, memo_scope="test",
    )


async def _executed(value):
    return value


@pytest.mark.asyncio
async def test_memoize_executes_and_records_origin():
    ctx = _ctx()
    result, origin = await ctx.memoize(
        "search", 1, {"q": "ev"}, lambda: _executed({"hits": 3}))
    assert result == {"hits": 3}
    assert origin == "executed"
    assert ctx.origins["search"] == ["executed"]


@pytest.mark.asyncio
async def test_repeated_calls_always_execute():
    ctx = _ctx()
    calls = {"n": 0}

    async def execute():
        calls["n"] += 1
        return {"n": calls["n"]}

    r1, _ = await ctx.memoize("search", 1, {"q": "ev"}, execute)
    r2, _ = await ctx.memoize("search", 1, {"q": "ev"}, execute)
    # No memo layer: identical logical calls both really execute.
    assert r1 == {"n": 1} and r2 == {"n": 2}
    assert ctx.origins["search"] == ["executed", "executed"]


@pytest.mark.asyncio
async def test_record_origin_opt_out():
    ctx = _ctx()
    await ctx.memoize("search", 1, {"q": "ev"},
                      lambda: _executed({}), record_origin=False)
    assert "search" not in ctx.origins


@pytest.mark.asyncio
async def test_producer_category_passes_through_too():
    # reuse="never" is the producer alias; on the open tier it also
    # executes (there is no cache to refuse).
    ctx = _ctx()
    result, origin = await ctx.memoize(
        "analyze", 1, {}, lambda: _executed({"v": 1}), reuse="never")
    assert result == {"v": 1}
    assert origin == "executed"


@pytest.mark.asyncio
async def test_stub_mode_unreachable_with_default_resolver():
    # The default resolver never returns stub, so the stub branch of
    # memoize is unreachable on the open tier: the call executes even
    # for an external-tagged purpose.
    ctx = _ctx()
    result, origin = await ctx.memoize(
        "send_email", 1, {"to": "x"},
        lambda: _executed({"sent": True}))
    assert result == {"sent": True}
    assert origin == "executed"


@pytest.mark.asyncio
async def test_record_stub_audit_degrades_without_recorder():
    # No tools wired: the direct-audit surface is absent and the call
    # must silently no-op (origins remain the fallback evidence).
    ctx = _ctx()
    await ctx.record_stub_audit("send_email", seq=3, inputs={})


@pytest.mark.asyncio
async def test_mode_argument_is_forwarded_to_memo_layer():
    # When a caller pre-resolved the mode (tracked atoms), the context
    # forwards it untouched instead of re-resolving. With no memo
    # layer the outcome is still execute, but the resolver must NOT be
    # consulted: prove that by injecting a resolver that would fail.
    class _ExplodingResolver:
        @property
        def active(self):
            return False

        @property
        def table(self):
            return {}

        def resolve(self, *args, **kwargs):
            raise AssertionError("resolver must not be consulted")

    meta = GenerationMeta(task_id="t", eid="e0001",
                          previous_eids=(), previous_status=None)
    ctx = AgentContext(
        meta, tools=None, llms={}, archive_backend=None,
        memo_scope="test", policy_resolver=_ExplodingResolver())
    result, origin = await ctx.memoize(
        "search", 1, {"q": "ev"}, lambda: _executed({}),
        mode="always", category=CallClass.READONLY)
    assert origin == "executed"