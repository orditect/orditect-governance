"""Spy-engine assembly proof over a FULL acceptance run.

Same contract as the runtime's test_engine_plugin suite (factories
invoked with generation facts, ctx.memoize delegating to the injected
layer, the resolver receiving the routing tuple) — but driven through
the complete workflow instead of a single agent, so the assembly is
locked across fan-out, pause/resume, reopen and the quality gate.
"""

from __future__ import annotations

import pytest

from examples.acceptance.app import (
    PUBLISH_ID,
    RESEARCHERS,
    REVIEW_ID,
    WRITER_ID,
    execute_acceptance_run,
)
from ordigovernance.api.side_effect import SideEffect


class SpyMemoLayer:
    """Records construction facts and memoize delegations."""

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


class SpyResolver:
    """Records construction facts and routing tuples."""

    def __init__(self):
        self.constructed_with: list[dict] = []
        self.calls: list[dict] = []

    def factory(self):
        def _make(table, *, override=None):
            self.constructed_with.append({"table": table,
                                          "override": override})
            return self
        return _make

    @property
    def active(self) -> bool:
        return False

    @property
    def table(self) -> dict:
        return {}

    def resolve(self, purpose, category, *,
                side_effect=SideEffect.READONLY, declared="always"):
        self.calls.append({"purpose": purpose, "category": category,
                           "side_effect": side_effect,
                           "declared": declared})
        return "always"


@pytest.mark.asyncio
async def test_spy_engine_assembly_over_full_run(tmp_path):
    memo_spy = SpyMemoLayer()
    resolver_spy = SpyResolver()
    record = await execute_acceptance_run(
        tmp_path / "trace",
        memo_layer_factory=memo_spy.factory(),
        resolver_factory=resolver_spy.factory(),
    )
    assert record["status"] == "succeeded"

    # Every GovernedAgent built its context through the factories, with
    # the generation facts the engine protocols require.
    by_task: dict[str, list[dict]] = {}
    for call in memo_spy.constructed_with:
        by_task.setdefault(call["task_id"], []).append(call)
    for tid in (*RESEARCHERS, WRITER_ID, REVIEW_ID, PUBLISH_ID):
        assert tid in by_task, f"factory never built a layer for {tid}"
        for call in by_task[tid]:
            assert call["backend"] is not None
            assert call["eid"].startswith("exec-")
            assert call["scope"] == "acceptance"
    # researcher-1 (scope-retry reopen) and researcher-2 (pause/resume)
    # each built a fresh layer per generation.
    assert len(by_task[RESEARCHERS[0]]) == 2
    assert len(by_task[RESEARCHERS[1]]) == 2

    # ctx.memoize delegated to the injected layer for every world read:
    # 3 initial researchers + 1 resume + 1 reopen; writer/review/publish
    # go through ctx.llm_call, never memoize.
    purposes = [c["purpose"] for c in memo_spy.calls]
    assert purposes == ["search"] * 5
    assert all(c["mode"] == "always" for c in memo_spy.calls)

    # The resolver received the full routing tuple per memoize call.
    assert len(resolver_spy.calls) == 5
    for call in resolver_spy.calls:
        assert call["purpose"] == "search"
        assert call["side_effect"] is SideEffect.READONLY
        assert call["declared"] == "always"

    # The resolver factory saw the (empty) policy channels of this run.
    assert len(resolver_spy.constructed_with) == \
        len(memo_spy.constructed_with)
    for call in resolver_spy.constructed_with:
        assert call["table"] is None
        assert call["override"] is None