import asyncio

from ordigovernance.runtime.patterns.quality_gate import (
    QualityGateConfig,
    QualityGatePattern,
)


def run(coro):
    return asyncio.run(coro)


class Receipt:
    def __init__(self, action_id):
        self.action_id = action_id


class FakeSink:
    def __init__(self):
        self.retries = []

    async def retry_scope(self, root_id, ids, *, actor):
        self.retries.append((root_id, set(ids), actor))
        return Receipt(f"action-{len(self.retries)}")

    async def get_receipt(self, action_id):
        return {"action_id": action_id, "status": "done"}


class FakeOrchestrator:
    def __init__(self, producer_status="succeeded"):
        self.submits = []
        self._producer_status = producer_status
        self._review_gen = 0

    async def submit(self, task, *, task_id, parent_task_id=None,
                     if_not_exists=False):
        self.submits.append(task_id)
        return task_id

    async def wait_terminal(self, task_id, *, timeout):
        if task_id == "judge":
            self._review_gen += 1
            return {"status": "succeeded",
                    "result": {"verdict": self._review_gen}}
        return {"status": self._producer_status, "result": {}}


def test_gate_passes_on_second_iteration():
    orch, sink = FakeOrchestrator(), FakeSink()
    gate = QualityGatePattern(
        orch, sink,
        config=QualityGateConfig(max_iterations=3, receipt_timeout=1.0),
    )
    outcome = run(gate.run(
        root_id="root", producer_id="prod", judge_id="judge",
        parent_task_id="root",
        build_producer=lambda: "prod-task",
        build_judge=lambda: "judge-task",
        score_of=lambda rec: rec["result"]["verdict"],
        is_pass=lambda v: v >= 2,
    ))
    assert outcome.passed is True
    assert outcome.iterations == 2
    assert outcome.scores == (1, 2)
    assert outcome.degraded is False
    # iteration 1 submits both; iteration 2 reopens both via the sink
    assert orch.submits == ["prod", "judge"]
    assert sink.retries == [("root", {"prod"}, "quality-gate"),
                            ("root", {"judge"}, "quality-gate")]


def test_gate_degrades_at_cap():
    orch, sink = FakeOrchestrator(), FakeSink()
    gate = QualityGatePattern(
        orch, sink,
        config=QualityGateConfig(max_iterations=2, receipt_timeout=1.0),
    )
    outcome = run(gate.run(
        root_id="root", producer_id="prod", judge_id="judge",
        parent_task_id="root",
        build_producer=lambda: "prod-task",
        build_judge=lambda: "judge-task",
        score_of=lambda rec: rec["result"]["verdict"],
        is_pass=lambda v: v >= 99,
    ))
    assert outcome.passed is False
    assert outcome.degraded is True


def test_gate_stops_on_producer_failure():
    orch = FakeOrchestrator(producer_status="failed")
    sink = FakeSink()
    gate = QualityGatePattern(orch, sink)
    outcome = run(gate.run(
        root_id="root", producer_id="prod", judge_id="judge",
        parent_task_id="root",
        build_producer=lambda: "prod-task",
        build_judge=lambda: "judge-task",
        score_of=lambda rec: None,
        is_pass=lambda v: False,
    ))
    assert outcome.passed is False
    assert outcome.degraded is False
    assert outcome.review_record is None


def test_gate_transports_business_verdict_objects():
    """Verdict objectification: the gate passes verdicts through untouched."""
    orch, sink = FakeOrchestrator(), FakeSink()
    gate = QualityGatePattern(
        orch, sink,
        config=QualityGateConfig(max_iterations=3, receipt_timeout=1.0),
    )
    producer_builds = []

    def verdict_of(rec):
        gen = rec["result"]["verdict"]
        return {"score": gen,
                "verdicts": {"coverage": "gap"} if gen < 2 else {}}

    def build_producer():
        producer_builds.append(len(producer_builds) + 1)
        return "prod-task"

    verdict_history = []

    def tracking_verdict_of(rec):
        v = verdict_of(rec)
        verdict_history.append(v)
        return v

    outcome = run(gate.run(
        root_id="root", producer_id="prod", judge_id="judge",
        parent_task_id="root",
        build_producer=build_producer,
        build_judge=lambda: "judge-task",
        score_of=tracking_verdict_of,
        is_pass=lambda v: v["score"] >= 2,
    ))
    assert outcome.passed is True
    assert outcome.iterations == 2
    # Verdicts arrive untouched (same objects, per round).
    assert list(outcome.verdicts) == verdict_history
    assert outcome.verdicts[0] == {"score": 1,
                                   "verdicts": {"coverage": "gap"}}
    # Iteration 2 reopened the producer instead of rebuilding it.
    assert producer_builds == [1]
    assert sink.retries == [("root", {"prod"}, "quality-gate"),
                            ("root", {"judge"}, "quality-gate")]
    # Legacy alias stays readable for single-score callers.
    assert outcome.scores == outcome.verdicts