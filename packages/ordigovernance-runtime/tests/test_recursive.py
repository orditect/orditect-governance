import asyncio

import pytest

from ordigovernance.runtime.patterns.recursive import RecursiveComposition


def run(coro):
    return asyncio.run(coro)


class FakeOrchestrator:
    def __init__(self, statuses):
        self._statuses = statuses
        self.submitted = []

    async def submit(self, task, *, task_id, **kwargs):
        self.submitted.append(task_id)
        return task_id

    async def wait_terminal(self, task_id, *, timeout):
        return {"task_id": task_id, "status": self._statuses[task_id]}


def test_sequential_composition_aggregates():
    orch = FakeOrchestrator({"s1": "succeeded", "s2": "succeeded"})
    comp = RecursiveComposition(orch)
    records = run(comp.run([("s1", lambda: "t1"), ("s2", lambda: "t2")]))
    assert list(records) == ["s1", "s2"]
    assert orch.submitted == ["s1", "s2"]


def test_fail_fast_raises_on_first_failure():
    orch = FakeOrchestrator({"s1": "failed", "s2": "succeeded"})
    comp = RecursiveComposition(orch)
    with pytest.raises(RuntimeError, match="s1 failed"):
        run(comp.run([("s1", lambda: "t1"), ("s2", lambda: "t2")]))
    assert orch.submitted == ["s1"]  # s2 never submitted