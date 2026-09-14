"""Engine plug-in assembly (the open tier's central architectural test).

The runtime ships mechanism-direct defaults; engines with richer
semantics (memo reuse, replay policy routing) are injected through
GovernedAgent's factories. This suite locks the ASSEMBLY contract with
spy implementations: factories are invoked with the right arguments,
ctx.memoize delegates to the injected memo layer, and the injected
resolver receives the full routing tuple. The engine tier's own suite
asserts the semantics of real implementations; together they cover
the full chain.
"""

from __future__ import annotations

import pytest

from ordigovernance.api.side_effect import CallClass, SideEffect
from ordigovernance.api.task import GenerationMeta
from ordigovernance.runtime.agent.governed_agent import GovernedAgent


class _SpyMemoLayer:
    def __init__(self):
        self.constructed_with: list[dict] = []
        self.calls: list[dict] = []

    def factory(self):
        def _make(backend, *, task_id, eid, previous_status, scope):
            self.constructed_with.append({
                "backend": backend, "task_id": task_id, "eid": eid,
                "previous_status": previous_status, "scope": scope,
            })
            return self
        return _make

    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        self.calls.append({"purpose": purpose, "seq": seq,
                           "inputs": inputs, "reuse": reuse,
                           "origin_seq": origin_seq, "mode": mode})
        return await execute(), "reused:spy-origin-1"


class _SpyResolver:
    def __init__(self):
        self.constructed_with: list[dict] = []
        self.calls: list[dict] = []
        self._active = False

    def factory(self):
        def _make(table, *, override=None):
            self.constructed_with.append({"table": table,
                                          "override": override})
            return self
        return _make

    @property
    def active(self) -> bool:
        return self._active

    @property
    def table(self) -> dict:
        return {}

    def resolve(self, purpose, category, *,
                side_effect=SideEffect.READONLY, declared="always"):
        self.calls.append({"purpose": purpose, "category": category,
                           "side_effect": side_effect,
                           "declared": declared})
        return "always"


class _FakeTaskIO:
    def __init__(self, record):
        self._record = record

    async def get_task(self, task_id):
        return self._record

    async def update_task(self, task_id, patch):
        self._record.update(patch)


class _FakeBackend:
    def __init__(self):
        self.store = {}

    async def memory_read(self, key, *, call_id, payload_fn=None):
        return {"key": key, "value": self.store.get(key)}

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.store[key] = value
        return {"key": key, "stored": True}


class _MemoizeImpl:
    async def run(self, ctx) -> dict:
        result, origin = await ctx.memoize(
            "search", 1, {"q": "ev"},
            lambda: _executed())
        return {"origin": origin, "result": result}


async def _executed():
    return {"hits": 3}


def _make_agent(impl, *, backend, memo_factory=None, resolver_factory=None,
                side_effect_policy=None, memo_policy=None):
    io = _FakeTaskIO({"execution_id": "exec-gen1",
                      "previous_execution_ids": [],
                      "previous_status": None})
    return GovernedAgent(
        impl=impl, task_io=io, tools=None, llms={},
        archive_backend=backend, memo_scope="run-scope",
        side_effect_policy=side_effect_policy,
        memo_policy=memo_policy,
        memo_layer_factory=memo_factory,
        resolver_factory=resolver_factory,
    )


class TestMemoLayerFactory:
    @pytest.mark.asyncio
    async def test_factory_invoked_with_generation_facts(self):
        spy = _SpyMemoLayer()
        backend = _FakeBackend()
        agent = _make_agent(_MemoizeImpl(), backend=backend,
                            memo_factory=spy.factory())
        await agent.execute("agent-a")
        assert spy.constructed_with == [{
            "backend": backend, "task_id": "agent-a",
            "eid": "exec-gen1", "previous_status": None,
            "scope": "run-scope",
        }]

    @pytest.mark.asyncio
    async def test_memoize_delegates_to_injected_layer(self):
        spy = _SpyMemoLayer()
        backend = _FakeBackend()
        agent = _make_agent(_MemoizeImpl(), backend=backend,
                            memo_factory=spy.factory())
        result = await agent.execute("agent-a")
        assert spy.calls == [{
            "purpose": "search", "seq": 1, "inputs": {"q": "ev"},
            "reuse": "always", "origin_seq": None, "mode": "always",
        }]
        # The injected layer's origin flows back through the context.
        assert result == {"origin": "reused:spy-origin-1",
                          "result": {"hits": 3}}


class TestResolverFactory:
    @pytest.mark.asyncio
    async def test_factory_invoked_with_policy_channels(self):
        spy = _SpyResolver()
        backend = _FakeBackend()
        agent = _make_agent(
            _MemoizeImpl(), backend=backend,
            resolver_factory=spy.factory(),
            side_effect_policy={"readonly": "always"},
            memo_policy="never")
        await agent.execute("agent-a")
        assert spy.constructed_with == [{
            "table": {"readonly": "always"}, "override": "never"}]

    @pytest.mark.asyncio
    async def test_memoize_resolves_through_injected_resolver(self):
        spy = _SpyResolver()
        backend = _FakeBackend()
        agent = _make_agent(_MemoizeImpl(), backend=backend,
                            resolver_factory=spy.factory())
        await agent.execute("agent-a")
        assert spy.calls == [{
            "purpose": "search", "category": CallClass.READONLY,
            "side_effect": SideEffect.READONLY, "declared": "always"}]

    @pytest.mark.asyncio
    async def test_context_exposes_injected_resolver(self):
        spy = _SpyResolver()
        backend = _FakeBackend()
        agent = _make_agent(_MemoizeImpl(), backend=backend,
                            resolver_factory=spy.factory())
        ctx = agent.build_context(GenerationMeta(
            task_id="agent-a", eid="exec-gen1", previous_eids=(),
            previous_status=None))
        assert ctx.policy_resolver is spy


class TestDefaultsWithoutFactories:
    @pytest.mark.asyncio
    async def test_no_factories_mechanism_direct(self):
        backend = _FakeBackend()
        agent = _make_agent(_MemoizeImpl(), backend=backend)
        result = await agent.execute("agent-a")
        # Passthrough: executes, origin recorded as executed.
        assert result["origin"] == "executed"