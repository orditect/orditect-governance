import asyncio

from ordigovernance.runtime.patterns.fanout import FanOutPattern


def run(coro):
    return asyncio.run(coro)


class FakeOrchestrator:
    def __init__(self, outcomes):
        self._outcomes = outcomes
        self.submitted = []

    async def submit(self, task, *, task_id, if_not_exists=False,
                     parent_task_id=None):
        self.submitted.append((task_id, if_not_exists))
        return task_id

    async def wait_terminal(self, task_id, *, timeout):
        await asyncio.sleep(0)
        return {"task_id": task_id, "status": self._outcomes[task_id],
                "execution_id": f"exec-{task_id}"}


class FakeTaskIO:
    def __init__(self):
        self.records = {}

    async def get_task(self, task_id):
        return self.records.get(task_id, {"status": "succeeded"})

    async def update_task(self, task_id, patch):
        self.records.setdefault(task_id, {}).update(patch)


class FakeEdgeIO:
    def __init__(self):
        self.edges = []

    async def write_dependency(self, edge):
        self.edges.append(edge)


def test_fanout_submits_edges_and_tallies():
    orch = FakeOrchestrator({"w-1": "succeeded", "w-2": "failed",
                             "w-3": "cancelled"})
    edges = FakeEdgeIO()
    pattern = FanOutPattern(orch, FakeTaskIO(), edge_io=edges)

    result = run(pattern.run(
        "sup", ["a", "b", "c"],
        build_child_id=lambda pos, item: f"w-{pos}",
        build_child=lambda pos, item, cid: f"task:{cid}",
    ))

    assert result.total == 3
    assert result.succeeded == 1
    assert result.failed == ("w-2",)
    assert result.cancelled == ("w-3",)
    assert orch.submitted == [("w-1", True), ("w-2", True), ("w-3", True)]
    assert [
        (e.child_id, e.parent_id, e.is_primary) for e in edges.edges
    ] == [
        ("w-1", "sup", True),
        ("w-2", "sup", True),
        ("w-3", "sup", True),
    ]


def test_wait_resumed_returns_when_children_succeed():
    orch = FakeOrchestrator({})
    io = FakeTaskIO()
    pattern = FanOutPattern(orch, io, step_timeout=5.0)
    run(pattern.wait_resumed("sup", ["w-1"], poll_interval=0.01))