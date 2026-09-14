"""Heterogeneous fan-out: mixed child types in ONE FanOutPattern.run."""

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
        self.submitted.append((task_id, task))
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


ITEMS = [
    {"type": "solution", "topic": "architecture"},
    {"type": "pricing", "topic": "cost model"},
    {"type": "risk", "topic": "compliance"},
]


def make_child(item, child_id):
    """Item-shaped builder: the heterogeneous construction site."""
    return {"task": item["type"], "id": child_id,
            "resource_type": f"exec-{item['type']}"}


def test_heterogeneous_items_one_round():
    orch = FakeOrchestrator({f"analyst-{it['type']}": "succeeded"
                             for it in ITEMS})
    edges = FakeEdgeIO()
    pattern = FanOutPattern(orch, FakeTaskIO(), edge_io=edges)

    result = run(pattern.run(
        "sup", ITEMS,
        build_child_id=lambda item: f"analyst-{item['type']}",
        build_child=make_child,
    ))

    assert result.child_ids == ("analyst-solution", "analyst-pricing",
                                "analyst-risk")
    assert result.succeeded == 3
    submitted = dict(orch.submitted)
    assert submitted["analyst-pricing"]["resource_type"] == "exec-pricing"
    assert submitted["analyst-risk"]["task"] == "risk"
    assert [(e.child_id, e.parent_id) for e in edges.edges] == [
        ("analyst-solution", "sup"),
        ("analyst-pricing", "sup"),
        ("analyst-risk", "sup"),
    ]


def test_legacy_positional_builders_still_work():
    orch = FakeOrchestrator({"w-1": "succeeded", "w-2": "succeeded"})
    pattern = FanOutPattern(orch, FakeTaskIO())

    result = run(pattern.run(
        "sup", ["a", "b"],
        build_child_id=lambda pos, item: f"w-{pos}",
        build_child=lambda pos, item, cid: f"task:{cid}:{item}",
    ))

    assert result.child_ids == ("w-1", "w-2")
    assert dict(orch.submitted) == {"w-1": "task:w-1:a",
                                    "w-2": "task:w-2:b"}


def test_build_child_without_child_id():
    orch = FakeOrchestrator({"only": "succeeded"})
    pattern = FanOutPattern(orch, FakeTaskIO())

    result = run(pattern.run(
        "sup", [{"type": "t"}],
        build_child_id=lambda item: "only",
        build_child=lambda item: f"task:{item['type']}",
    ))

    assert result.child_ids == ("only",)
    assert dict(orch.submitted) == {"only": "task:t"}