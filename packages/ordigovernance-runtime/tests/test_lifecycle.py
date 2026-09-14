import asyncio
import json

from ordigovernance.runtime.lifecycle.cleanup import (
    CleanupService,
    collect_known_task_ids,
)
from ordigovernance.runtime.lifecycle.event_bus import EventBus, jsonable
from ordigovernance.runtime.lifecycle.run_registry import RunsRegistry, new_run_id


def run(coro):
    return asyncio.run(coro)


def test_runs_registry_roundtrip(tmp_path):
    reg = RunsRegistry(tmp_path / "runs")
    rid = new_run_id()
    reg.register_run(rid, "intent text", {"k": 1}, "root:marker")
    entry = reg.get_run(rid)
    assert entry["status"] == "running"
    assert entry["budget_scope"] == "root:marker"
    reg.finish_run(rid, final_status="succeeded", budget_balance=97)
    entry = reg.get_run(rid)
    assert entry["final_status"] == "succeeded"
    assert entry["budget_balance"] == 97
    assert reg.list_runs()[0]["run_id"] == rid
    assert reg.run_trace_dir(rid).name == "trace"


def test_runs_registry_corrupt_index_degrades(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "index.json").write_text("{not json")
    reg = RunsRegistry(runs_dir)
    assert reg.list_runs() == []


def test_event_bus_fanout_and_jsonable():
    async def main():
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish({"type": "x"})
        event = await asyncio.wait_for(q.get(), timeout=1)
        bus.unsubscribe(q)
        return event

    assert run(main()) == {"type": "x"}
    assert jsonable({"a": (1, 2)}) == {"a": [1, 2]}


def test_collect_known_task_ids(tmp_path):
    snap = tmp_path / "snapshots.ndjson"
    lines = [
        json.dumps({"data": {"task_id": "w-1"}}),
        json.dumps({"data": {"task_id": "w-2"}}),
        "not json",
    ]
    snap.write_text("\n".join(lines))
    ids = collect_known_task_ids(lambda: [snap, tmp_path / "missing.ndjson"],
                                 {"static-1"})
    assert ids == {"w-1", "w-2", "static-1"}


class FakeRedis:
    def __init__(self):
        self.deleted = []

    async def delete(self, *keys):
        self.deleted.append(keys)


def test_cleanup_service_deletes_by_explicit_key():
    client = FakeRedis()
    run(CleanupService().reset_task_state(client, {"b", "a"}))
    assert client.deleted == [("task:a", "task:b")]


def test_cleanup_service_empty_and_failure_tolerant():
    class FailingRedis:
        async def delete(self, *keys):
            raise RuntimeError("down")

    run(CleanupService().reset_task_state(FailingRedis(), {"x"}))
    client = FakeRedis()
    run(CleanupService().reset_task_state(client, set()))
    assert client.deleted == []