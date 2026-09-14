"""ReplayDriver mechanics (open tier) with a spy drift engine.

Locks the mechanism contracts: local ids, submit/reopen sequences,
baseline resolution, ledger events, build_task signature transport
(legacy and channel-aware), async build_task awaiting, and the
evidence assembly handed to the injected engine. Attribution
semantics (verdicts, confidences) are the engine tier's suite.
"""

import asyncio
from types import SimpleNamespace

from ordigovernance.runtime.replay.driver import ReplayDriver
from ordigovernance.runtime.replay.spec import ReplayInput, ReplaySpec


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
        self.submissions = []

    async def submit(self, task, *, task_id, parent_task_id=None,
                     if_not_exists=False):
        self.submissions.append(task_id)
        self.hot.gen += 1
        self.hot.records[task_id] = {
            "execution_id": f"exec-g{self.hot.gen}",
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
        self.calls = []

    async def retry_scope(self, root_id, ids, *, actor):
        self.calls.append((root_id, set(ids), actor))
        for tid in ids:
            rec = self.hot.records[tid]
            rec["previous_execution_ids"] = (
                rec.get("previous_execution_ids", [])
                + [rec["execution_id"]]
            )
            self.hot.gen += 1
            rec["execution_id"] = f"exec-g{self.hot.gen}"
            rec["status"] = "running"
        return SimpleNamespace(action_id=f"a{len(self.calls)}")

    async def get_receipt(self, action_id):
        return {"action_id": action_id, "status": "done"}


class FakeBackend:
    def __init__(self):
        self.store = {}

    async def memory_read(self, key, *, call_id, payload_fn=None):
        return {"key": key, "value": self.store.get(key)}

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.store[key] = value
        return {"key": key, "stored": True}


class _FakeAuditReader:
    def __init__(self, events):
        self._events = events

    async def query(self, *, task_id=None):
        return [e for e in self._events
                if task_id is None or task_id in e["data"]["event_id"]]


class SpyDriftEngine:
    """Records the evidence assembly; returns marker reports."""

    def __init__(self):
        self.build_calls: list[dict] = []
        self.compose_calls: list[dict] = []

    def build(self, local_id, generations, statuses, *,
              audit_events, results):
        self.build_calls.append({
            "local_id": local_id, "generations": generations,
            "statuses": statuses, "audit_events": audit_events,
            "results": results,
        })
        return {"verdict": "spy", "local_id": local_id}

    def compose_range(self, order, edges, node_reports, *,
                      baseline_rows, audit_events, node_pins,
                      pin_task_ids):
        self.compose_calls.append({
            "order": order, "edges": edges,
            "node_reports": node_reports,
            "baseline_rows": baseline_rows,
            "audit_events": audit_events,
            "node_pins": node_pins, "pin_task_ids": pin_task_ids,
        })
        return {"verdict": "spy-range", "order": order}


def make_driver(hot=None, backend=None, drift=None, events=()):
    hot = hot or FakeHot()
    driver = ReplayDriver(
        FakeOrchestrator(hot), FakeSink(hot), hot,
        archive_backend=backend,
        audit_reader=_FakeAuditReader(list(events)) if events or drift
        else None,
        drift_engine=drift,
        id_prefix="rp", receipt_timeout=1.0, poll_interval=0.01,
    )
    return driver, hot


def test_replay_node_explicit_input_times_three():
    driver, hot = make_driver()
    captured = []

    def build(task_id, *, pinned_input, memo_policy, upstream_outputs,
              local_id):
        captured.append({"pinned": pinned_input, "memo": memo_policy,
                         "local_id": local_id})
        return SimpleNamespace()

    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_explicit({"seed": 1}),
        times=3, memo_policy="always",
    )
    report = run(driver.replay_node(spec, build, parent_id="root"))

    assert captured == [{"pinned": {"seed": 1}, "memo": "always",
                         "local_id": "node-a-rp"}]
    assert report.local_id == "node-a-rp"
    assert len(report.generations) == 3
    assert report.statuses == ("succeeded",) * 3
    # one submit plus two sink-driven reopens on the same local id
    assert len(driver._orchestrator.submissions) == 1
    assert driver._sink.calls == [("root", {"node-a-rp"}, "replay")] * 2


def test_replay_node_archive_input_resolution():
    backend = FakeBackend()
    backend.store["gen-result/node-a/exec-old"] = {
        "result": {"seed": 7}, "input_pins": {},
    }
    driver, _ = make_driver(backend=backend)
    captured = []

    def build(task_id, *, pinned_input, **kwargs):
        captured.append(pinned_input)
        return SimpleNamespace()

    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_archive("node-a", "exec-old"),
    )
    run(driver.replay_node(spec, build, parent_id="root"))
    assert captured == [{"seed": 7}]


def test_replay_range_start_pinned_downstream_fed():
    driver, _ = make_driver()
    edges = [{"child_id": "b", "parent_id": "a", "is_primary": True},
             {"child_id": "c", "parent_id": "b", "is_primary": True}]
    builds = {}

    def build(task_id, *, pinned_input, upstream_outputs, **kwargs):
        builds[task_id] = {"pinned": pinned_input,
                           "upstream": dict(upstream_outputs)}
        return SimpleNamespace()

    report = run(driver.replay_range(
        "a", "c", edges=edges,
        input=ReplayInput.from_explicit({"seed": 1}),
        build_task=build, parent_id="root",
    ))

    assert report.order == ("a", "b", "c")
    assert builds["a"]["pinned"] == {"seed": 1}
    assert builds["b"]["pinned"] is None
    # downstream consumes upstream replay outputs (local ids in results)
    assert builds["b"]["upstream"] == {"a": {"out": "a-rp"}}
    assert builds["c"]["upstream"] == {"a": {"out": "a-rp"},
                                       "b": {"out": "b-rp"}}
    assert len(report.rounds) == 1
    assert [r.task_id for r in report.rounds[0]] == ["a", "b", "c"]


def test_replay_range_times_two_uses_fresh_prefixes():
    driver, _ = make_driver()
    edges = [{"child_id": "b", "parent_id": "a", "is_primary": True}]

    report = run(driver.replay_range(
        "a", "b", edges=edges,
        input=ReplayInput.from_explicit({}),
        build_task=lambda *a, **k: SimpleNamespace(),
        parent_id="root", times=2,
    ))
    assert len(report.rounds) == 2
    assert report.rounds[0][0].local_id == "a-rp-r1"
    assert report.rounds[1][0].local_id == "a-rp-r2"


def test_replay_channel_fields_forwarded_when_declared():
    """llm_params / side_effect_policy transport opaquely to build_task."""
    driver, _ = make_driver()
    captured = {}

    def build(task_id, *, pinned_input, memo_policy, upstream_outputs,
              local_id, side_effect_policy, llm_params):
        captured.update(pinned=pinned_input, local_id=local_id,
                        se=side_effect_policy, lp=llm_params)
        return SimpleNamespace()

    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_explicit({"seed": 1}),
        side_effect_policy={"external": "stub"},
        llm_params={"temperature": 0},
        label="sampling-freeze probe",
    )
    run(driver.replay_node(spec, build, parent_id="root"))

    assert captured == {
        "pinned": {"seed": 1}, "local_id": "node-a-rp",
        "se": {"external": "stub"}, "lp": {"temperature": 0},
    }


def test_replay_channel_fields_omitted_for_legacy_build_task():
    """Legacy callbacks without the new parameters still work."""
    driver, _ = make_driver()
    captured = {}

    def build(task_id, *, pinned_input, memo_policy, upstream_outputs,
              local_id):
        captured.update(pinned=pinned_input, local_id=local_id)
        return SimpleNamespace()

    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_explicit({"seed": 1}),
        llm_params={"temperature": 0},
        side_effect_policy={"external": "stub"},
    )
    report = run(driver.replay_node(spec, build, parent_id="root"))

    assert captured == {"pinned": {"seed": 1}, "local_id": "node-a-rp"}
    assert report.statuses == ("succeeded",)


def test_async_build_task_is_awaited_before_submit():
    """An async build_task returns a coroutine: the driver must await
    it so the submitted object is the task itself."""
    driver, _ = make_driver()
    submitted = []

    original_submit = driver._orchestrator.submit

    async def tracking_submit(task, **kwargs):
        submitted.append(task)
        return await original_submit(task, **kwargs)

    driver._orchestrator.submit = tracking_submit

    async def build(task_id, **kwargs):
        return SimpleNamespace(marker="real-task")

    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_explicit({}),
    )
    report = run(driver.replay_node(spec, build, parent_id="root"))
    assert getattr(submitted[0], "marker", None) == "real-task"
    assert report.statuses == ("succeeded",)


def test_replay_range_forwards_llm_params_to_every_node():
    driver, _ = make_driver()
    edges = [{"child_id": "b", "parent_id": "a", "is_primary": True}]
    seen = []

    def build(task_id, *, llm_params, **kwargs):
        seen.append((task_id, llm_params))
        return SimpleNamespace()

    run(driver.replay_range(
        "a", "b", edges=edges,
        input=ReplayInput.from_explicit({}),
        build_task=build, parent_id="root",
        llm_params={"seed": 42},
    ))
    assert seen == [("a", {"seed": 42}), ("b", {"seed": 42})]


def test_experiment_ledger_receives_declaration():
    written = []

    async def ledger(event):
        written.append(event)

    driver, _ = make_driver()
    driver._ledger_writer = ledger
    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_explicit({"seed": 1}),
        label="variance probe",
        llm_params={"temperature": 0},
    )
    run(driver.replay_node(
        spec, lambda *a, **k: SimpleNamespace(), parent_id="root"))

    assert len(written) == 1
    event = written[0]
    assert event["type"] == "replay_experiment"
    assert event["task_id"] == "node-a"
    assert event["label"] == "variance probe"
    assert event["llm_params"] == {"temperature": 0}


def test_policy_isolated_scope_is_deterministic_and_discriminating():
    from ordigovernance.runtime.replay.driver import policy_isolated_scope

    base = "abc12345"
    s1 = policy_isolated_scope(
        base, side_effect_policy={"external": "stub"}, llm_params=None)
    # Same declaration -> same scope (a rerun hits its own cache).
    assert policy_isolated_scope(
        base, side_effect_policy={"external": "stub"},
        llm_params=None) == s1
    assert s1.startswith("abc12345/exp-")
    # Policy difference -> different domain.
    assert policy_isolated_scope(
        base, side_effect_policy={"external": "allow"},
        llm_params=None) != s1
    # llm_params difference -> different domain.
    assert policy_isolated_scope(
        base, side_effect_policy={"external": "stub"},
        llm_params={"temperature": 0}) != s1


def test_drift_none_without_engine():
    driver, _ = make_driver()
    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_explicit({"seed": 1}),
        times=2,
    )
    report = run(driver.replay_node(
        spec, lambda *a, **k: SimpleNamespace(), parent_id="root"))
    assert report.drift is None


def test_engine_receives_node_evidence_assembly():
    backend = FakeBackend()
    backend.store["gen-result/node-a-rp/exec-g1"] = {
        "result": {"out": "same"}, "input_pins": {}}
    backend.store["gen-result/node-a-rp/exec-g2"] = {
        "result": {"out": "same"}, "input_pins": {}}
    events = [
        {"data": {"event_id": "research-node-a-rp-exec-g1-1",
                  "event_type": "llm_call"}},
        {"data": {"event_id": "research-node-a-rp-exec-g2-1",
                  "event_type": "llm_call"}},
    ]
    spy = SpyDriftEngine()
    driver, _ = make_driver(backend=backend, drift=spy, events=events)

    spec = ReplaySpec(
        task_id="node-a",
        input=ReplayInput.from_explicit({"seed": 1}),
        times=2,
    )
    report = run(driver.replay_node(
        spec, lambda *a, **k: SimpleNamespace(), parent_id="root"))

    assert report.drift == {"verdict": "spy", "local_id": "node-a-rp"}
    assert len(spy.build_calls) == 1
    call = spy.build_calls[0]
    assert call["local_id"] == "node-a-rp"
    assert call["generations"] == ("exec-g1", "exec-g2")
    assert call["statuses"] == ("succeeded", "succeeded")
    assert call["results"] == {"exec-g1": {"out": "same"},
                               "exec-g2": {"out": "same"}}
    assert len(call["audit_events"]) == 2


def test_range_engine_receives_cumulative_evidence():
    backend = FakeBackend()
    backend.store["gen-result/a/exec-g1"] = {
        "result": {"out": "a-main"}, "input_pins": {}}
    backend.store["gen-result/b/exec-g2"] = {
        "result": {"out": "b-main"}, "input_pins": {}}
    backend.store["gen-result/a-rp/exec-g3"] = {
        "result": {"out": "a-rp"}, "input_pins": {"x": "e-x1"}}
    backend.store["gen-result/b-rp/exec-g4"] = {
        "result": {"out": "b-rp"}, "input_pins": {}}
    events = [
        {"data": {"event_id": "memget-a-rp-exec-g3-100",
                  "event_type": "memory_call",
                  "payload": {"decision": "miss"}}},
        {"data": {"event_id": "memget-b-rp-exec-g4-100",
                  "event_type": "memory_call",
                  "payload": {"decision": "miss"}}},
    ]
    hot = FakeHot()
    spy = SpyDriftEngine()
    driver = ReplayDriver(
        FakeOrchestrator(hot), FakeSink(hot), hot,
        archive_backend=backend,
        audit_reader=_FakeAuditReader(events),
        drift_engine=spy,
        id_prefix="rp", receipt_timeout=1.0, poll_interval=0.01,
    )
    edges = [{"child_id": "b", "parent_id": "a", "is_primary": True}]

    async def main():
        # Mainline generations establish the drift baseline.
        await driver._orchestrator.submit(SimpleNamespace(), task_id="a")
        await driver._orchestrator.wait_terminal("a", timeout=1.0)
        await driver._orchestrator.submit(SimpleNamespace(), task_id="b")
        await driver._orchestrator.wait_terminal("b", timeout=1.0)
        return await driver.replay_range(
            "a", "b", edges=edges,
            input=ReplayInput.from_explicit({"seed": 1}),
            build_task=lambda *a, **k: SimpleNamespace(),
            parent_id="root",
        )

    report = run(main())

    # Each node got one build call with baseline row first.
    assert len(spy.build_calls) == 2
    by_local = {c["local_id"]: c for c in spy.build_calls}
    a_call = by_local["a-rp"]
    assert a_call["generations"] == (("a", "exec-g1"),
                                     ("a-rp", "exec-g3"))
    assert a_call["statuses"] == ("succeeded", "succeeded")
    assert a_call["results"]["exec-g1"] == {"out": "a-main"}
    assert a_call["results"]["exec-g3"] == {"out": "a-rp"}

    # Range composition got the assembled node reports plus pins.
    assert len(spy.compose_calls) == 1
    compose = spy.compose_calls[0]
    assert compose["order"] == ("a", "b")
    assert compose["baseline_rows"] == 1
    assert compose["node_pins"] == {"a-rp/exec-g3": {"x": "e-x1"}}
    assert compose["pin_task_ids"] == {"a-rp": "a", "b-rp": "b"}
    assert report.drift == {"verdict": "spy-range", "order": ("a", "b")}
    # Per-round reports carry the spy drift too.
    assert report.rounds[0][0].drift == {"verdict": "spy",
                                         "local_id": "a-rp"}
    assert report.rounds[0][1].drift == {"verdict": "spy",
                                         "local_id": "b-rp"}


def test_range_drift_none_without_engine():
    driver, _ = make_driver()
    edges = [{"child_id": "b", "parent_id": "a", "is_primary": True}]
    report = run(driver.replay_range(
        "a", "b", edges=edges,
        input=ReplayInput.from_explicit({}),
        build_task=lambda *a, **k: SimpleNamespace(),
        parent_id="root",
    ))
    assert report.drift is None
    assert all(rep.drift is None for rep in report.rounds[0])