"""Composite endpoints: drive-level background drivers (M5)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.registry import (
    CompositeSpec,
    GatewayRegistry,
    ImplSpec,
    ToolSpec,
)
from ordigovernance.gateway.schemas import TaskDescriptor


async def _search_handler(query: str) -> dict:
    return {"query": query, "hits": 3}


class _EchoImpl:
    """Test impl: archives its params and settles immediately."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._params = params

    async def run(self, ctx) -> dict:
        result = {"marker": self._params.get("marker", "none")}
        await ctx.archive(result, pins={})
        return result


class _ScorerImpl:
    """Test judge impl: scripted per-round scores.

    The round marker derives from the PRODUCER's hot record
    (docs/pitfalls.md 13.3): producer generation N settles before
    judge generation N runs.
    """

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._scores = list(params.get("scores", [90]))
        self._producer_id = params.get("producer_id")
        self._storage = surfaces["storage"]

    async def run(self, ctx) -> dict:
        generation = 1
        if self._producer_id:
            record = await self._storage.get_task(self._producer_id)
            generation = 1 + len(
                record.get("previous_execution_ids", []))
        index = min(generation - 1, len(self._scores) - 1)
        result = {"score": self._scores[index], "generation": generation}
        await ctx.archive(result, pins={})
        return result


def _quality_gate_factory(params: dict, session):
    """Test composite: a quality-gate pair over registry impls.

    Mirrors the acceptance drive layer: the gate submits on the first
    iteration and reopens through the action sink afterwards, so the
    audit stream records the sink-driven reopen beats.
    """
    producer_id = params.get("producer_id", "prod")
    judge_id = params.get("judge_id", "judge")

    async def _drive() -> dict:
        from ordigovernance.runtime.patterns.quality_gate import (
            QualityGateConfig,
            QualityGatePattern,
        )

        session.register_descriptor(TaskDescriptor(
            task_id=producer_id, impl="echo",
            params={"marker": "draft"}))
        session.register_descriptor(TaskDescriptor(
            task_id=judge_id, impl="scorer",
            params={"scores": params.get("scores", [0, 90]),
                    "producer_id": producer_id}))
        gate = QualityGatePattern(
            session.resources.orchestrator, session.resources.sink,
            config=QualityGateConfig(max_iterations=3,
                                     step_timeout=60.0,
                                     receipt_timeout=15.0),
        )
        outcome = await gate.run(
            root_id=session.root_id,
            producer_id=producer_id, judge_id=judge_id,
            parent_task_id=session.root_id,
            build_producer=lambda: session.assemble_task(producer_id),
            build_judge=lambda: session.assemble_task(judge_id),
            score_of=lambda rec: (rec.get("result") or {}).get("score", 0),
            is_pass=lambda score: score >= 80,
        )
        return {"passed": outcome.passed,
                "iterations": outcome.iterations,
                "scores": list(outcome.scores)}

    return _drive()


def _boom_factory(params: dict, session):
    """Test composite: fails immediately."""

    async def _drive() -> dict:
        raise RuntimeError("composite exploded")

    return _drive()


@pytest.fixture
def composite_client(settings):
    registry = GatewayRegistry(
        tools={
            "search": ToolSpec(
                factory=lambda session: _search_handler,
                resource="web_search", event_type="tool_call",
                side_effect="readonly"),
        },
        impls={
            "echo": ImplSpec(
                factory=lambda params, surfaces: _EchoImpl(params,
                                                           surfaces)),
            "scorer": ImplSpec(
                factory=lambda params, surfaces: _ScorerImpl(params,
                                                             surfaces)),
        },
        composites={
            "quality-gate": CompositeSpec(
                factory=_quality_gate_factory,
                description="producer/judge gate pair"),
            "boom": CompositeSpec(factory=_boom_factory),
        },
    )
    app = build_app(settings, registry=registry)
    with TestClient(app) as c:
        yield c


def _start_run(client, headers, run_id="run-c1"):
    resp = client.post("/runs", json={"run_id": run_id}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["run_id"]


def _wait_composite(client, run_id, composite_id, headers, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/composites/{composite_id}",
                          headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"composite {composite_id} never settled")


def test_quality_gate_composite_converges_with_reopen_evidence(
        composite_client, auth_headers, settings):
    run_id = _start_run(composite_client, auth_headers)
    resp = composite_client.post(
        f"/runs/{run_id}/composites",
        json={"name": "quality-gate",
              "params": {"producer_id": "prod", "judge_id": "judge",
                         "scores": [0, 90]}},
        headers=auth_headers)
    assert resp.status_code == 202
    composite_id = resp.json()["composite_id"]
    assert resp.json()["accepted"] is True

    body = _wait_composite(composite_client, run_id, composite_id,
                           auth_headers)
    assert body["status"] == "succeeded"
    assert body["outcome"]["passed"] is True
    assert body["outcome"]["iterations"] == 2
    assert body["outcome"]["scores"] == [0, 90]

    # Children tracked and settled; the gate's second iteration
    # reopened the producer (M5: sink-driven reopen evidence).
    assert [c["task_id"] for c in body["children"]] == ["prod", "judge"]
    assert all(c["status"] == "succeeded" for c in body["children"])
    prod = composite_client.get(f"/runs/{run_id}/tasks/prod",
                                headers=auth_headers).json()
    assert len(prod["previous_execution_ids"]) == 1

    # Every generation archived itself at the archive band.
    audit_path = settings.trace_root / run_id / "trace" / "audit.ndjson"
    event_ids = []
    for line in audit_path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            event_ids.append(row.get("data", row).get("event_id", ""))
    saves = [e for e in event_ids if e.startswith("memsave-prod-")]
    assert len(saves) == 2
    assert all(e.endswith("-90") for e in saves)


def test_unknown_composite_is_422_lists_vocabulary(composite_client,
                                                   auth_headers):
    run_id = _start_run(composite_client, auth_headers)
    resp = composite_client.post(f"/runs/{run_id}/composites",
                                 json={"name": "nope", "params": {}},
                                 headers=auth_headers)
    assert resp.status_code == 422
    assert "quality-gate" in resp.json()["detail"]


def test_unknown_composite_id_is_404(composite_client, auth_headers):
    run_id = _start_run(composite_client, auth_headers)
    resp = composite_client.get(f"/runs/{run_id}/composites/nope",
                                headers=auth_headers)
    assert resp.status_code == 404


def test_failing_composite_settles_failed_with_error(composite_client,
                                                     auth_headers):
    run_id = _start_run(composite_client, auth_headers)
    resp = composite_client.post(f"/runs/{run_id}/composites",
                                 json={"name": "boom", "params": {}},
                                 headers=auth_headers)
    assert resp.status_code == 202
    body = _wait_composite(composite_client, run_id,
                           resp.json()["composite_id"], auth_headers)
    assert body["status"] == "failed"
    assert "composite exploded" in body["outcome"]["error"]


def test_composites_on_non_active_run_are_404(composite_client,
                                              auth_headers):
    run_id = _start_run(composite_client, auth_headers)
    resp = composite_client.post(f"/runs/{run_id}/finish",
                                 headers=auth_headers)
    assert resp.status_code == 200
    resp = composite_client.post(f"/runs/{run_id}/composites",
                                 json={"name": "quality-gate",
                                       "params": {}},
                                 headers=auth_headers)
    assert resp.status_code == 404