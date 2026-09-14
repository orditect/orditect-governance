"""Explicit range-drift baselines (regression).

When the hot path is shared across runs, the hot records hold the
LATEST generation of whichever run touched them last -- hot-derived
baselines contaminate a run's drift anchor with another run's
generations. The driver accepts pre-resolved baselines (derived from
the run's OWN snapshot bundle by the caller) and must prefer them
over the hot records. The spy engine records which baseline rows the
driver assembled; attribution semantics are the engine tier's suite.
"""

import asyncio
from types import SimpleNamespace

from ordigovernance.runtime.replay.driver import ReplayDriver
from ordigovernance.runtime.replay.spec import ReplayInput


def run(coro):
    return asyncio.run(coro)


class FakeHot:
    def __init__(self):
        self.records = {}
        self.gen = 0

    async def get_task(self, task_id):
        return self.records.get(task_id, {})

    async def update_task(self, task_id, patch):
        self.records.setdefault(task_id, {}).update(patch)


class FakeOrchestrator:
    def __init__(self, hot):
        self.hot = hot

    async def submit(self, task, *, task_id, parent_task_id=None,
                     if_not_exists=False):
        self.hot.gen += 1
        self.hot.records[task_id] = {
            "execution_id": f"exec-{task_id}-g{self.hot.gen}",
            "previous_execution_ids": [],
            "status": "running",
        }
        return task_id

    async def wait_terminal(self, task_id, *, timeout):
        rec = self.hot.records[task_id]
        rec["status"] = "succeeded"
        rec["result"] = {"out": task_id}
        return rec


class FakeSink:
    def __init__(self, hot):
        self.hot = hot

    async def retry_scope(self, root_id, ids, *, actor):
        return SimpleNamespace(action_id="a1")

    async def get_receipt(self, action_id):
        return {"action_id": action_id, "status": "done"}


class FakeBackend:
    def __init__(self):
        self.store = {}

    async def memory_read(self, key, *, call_id, payload_fn=None):
        return {"key": key, "value": self.store.get(key)}

    async def memory_write(self, key, value, *, call_id):
        self.store[key] = value
        return {"key": key, "stored": True}


class _FakeAuditReader:
    async def query(self, *, task_id=None):
        return []


class _SpyDrift:
    def __init__(self):
        self.build_calls: list[dict] = []

    def build(self, local_id, generations, statuses, *,
              audit_events, results):
        self.build_calls.append({"local_id": local_id,
                                 "generations": generations,
                                 "statuses": statuses,
                                 "results": results})
        return {"verdict": "spy"}

    def compose_range(self, order, edges, node_reports, **kwargs):
        return {"verdict": "spy-range"}


def _make_driver(hot, backend, spy):
    return ReplayDriver(
        FakeOrchestrator(hot), FakeSink(hot), hot,
        archive_backend=backend,
        audit_reader=_FakeAuditReader(),
        drift_engine=spy,
        id_prefix="rp", receipt_timeout=1.0, poll_interval=0.01,
    )


def test_explicit_baselines_beat_hot_records():
    backend = FakeBackend()
    # Archived results for the injected baseline generations plus the
    # replay round's generations.
    backend.store["gen-result/a/exec-a-base"] = {
        "result": {"out": "a-base"}, "input_pins": {}}
    backend.store["gen-result/b/exec-b-base"] = {
        "result": {"out": "b-base"}, "input_pins": {}}
    hot = FakeHot()
    spy = _SpyDrift()
    driver = _make_driver(hot, backend, spy)
    edges = [{"child_id": "b", "parent_id": "a", "is_primary": True}]
    # The hot records carry ANOTHER RUN's generations; they must be
    # ignored in favor of the injected baselines.
    hot.records["a"] = {"execution_id": "exec-a-foreign",
                        "status": "succeeded"}
    hot.records["b"] = {"execution_id": "exec-b-foreign",
                        "status": "succeeded"}

    run(driver.replay_range(
        "a", "b", edges=edges,
        input=ReplayInput.from_explicit({}),
        build_task=lambda *a, **k: SimpleNamespace(),
        parent_id="root",
        baselines={"a": ("exec-a-base", "succeeded"),
                   "b": ("exec-b-base", "succeeded")},
    ))

    by_local = {c["local_id"]: c for c in spy.build_calls}
    # Baseline row first: the injected eid, never the hot-record one.
    a_gens = by_local["a-rp"]["generations"]
    b_gens = by_local["b-rp"]["generations"]
    assert a_gens[0] == ("a", "exec-a-base")
    assert b_gens[0] == ("b", "exec-b-base")
    assert a_gens[0][1] != "exec-a-foreign"
    assert b_gens[0][1] != "exec-b-foreign"
    assert by_local["a-rp"]["results"]["exec-a-base"] == {
        "out": "a-base"}


def test_omitted_baselines_fall_back_to_hot_records():
    backend = FakeBackend()
    backend.store["gen-result/a/exec-a-hot"] = {
        "result": {"out": "a-hot"}, "input_pins": {}}
    backend.store["gen-result/b/exec-b-hot"] = {
        "result": {"out": "b-hot"}, "input_pins": {}}
    hot = FakeHot()
    hot.records["a"] = {"execution_id": "exec-a-hot",
                        "status": "succeeded"}
    hot.records["b"] = {"execution_id": "exec-b-hot",
                        "status": "succeeded"}
    spy = _SpyDrift()
    driver = _make_driver(hot, backend, spy)
    edges = [{"child_id": "b", "parent_id": "a", "is_primary": True}]

    run(driver.replay_range(
        "a", "b", edges=edges,
        input=ReplayInput.from_explicit({}),
        build_task=lambda *a, **k: SimpleNamespace(),
        parent_id="root",
    ))

    by_local = {c["local_id"]: c for c in spy.build_calls}
    # Backwards compatibility: no explicit baselines -> hot records.
    assert by_local["a-rp"]["generations"][0] == ("a", "exec-a-hot")
    assert by_local["b-rp"]["generations"][0] == ("b", "exec-b-hot")