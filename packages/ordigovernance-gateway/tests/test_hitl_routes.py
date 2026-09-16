"""HITL plane endpoints: pause / resume / retry, run-scoped."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.registry import (
    GatewayRegistry,
    ImplSpec,
    ToolSpec,
)
from ordigovernance.runtime.orchestration.cooperative_cancel import (
    cooperative_delay,
)


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


class _PausableImpl:
    """Test impl with a cooperative cancel window (the pause beat)."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._delay = float(params.get("delay", 1.5))
        self._storage = surfaces["storage"]

    async def run(self, ctx) -> dict:
        result = {"marker": "pausable"}
        await ctx.archive(result, pins={})
        await cooperative_delay(self._storage, ctx.meta.task_id,
                                self._delay, slice_seconds=0.05)
        return result


@pytest.fixture
def hitl_client(settings):
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
            "pausable": ImplSpec(
                factory=lambda params, surfaces: _PausableImpl(params,
                                                               surfaces)),
        },
    )
    app = build_app(settings, registry=registry)
    with TestClient(app) as c:
        yield c


def _start_run(client, headers, run_id="run-h1"):
    resp = client.post("/runs", json={"run_id": run_id}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["run_id"]


def _wait_status(client, run_id, task_id, headers, want, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/tasks/{task_id}",
                          headers=headers)
        assert resp.status_code == 200
        if resp.json()["status"] == want:
            return resp.json()
        time.sleep(0.05)
    raise AssertionError(f"{task_id} never reached status {want}")


def _wait_terminal(client, run_id, task_id, headers, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/tasks/{task_id}",
                          headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"{task_id} never settled")


def _wait_new_generation(client, run_id, task_id, old_eid, headers,
                         timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/tasks/{task_id}",
                          headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if (body["execution_id"] != old_eid
                and body["status"] in ("succeeded", "failed", "cancelled")):
            return body
        time.sleep(0.05)
    raise AssertionError(f"{task_id} never settled a new generation")


def _wait_receipt(client, run_id, action_id, headers, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/hitl/receipt/{action_id}",
                          headers=headers)
        if resp.status_code == 200:
            return resp.json()
        assert resp.status_code == 404
        time.sleep(0.1)
    raise AssertionError(f"receipt {action_id} never arrived")


def test_pause_settles_cancelled_and_resume_reruns_second_generation(
        hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    hitl_client.post(f"/runs/{run_id}/tasks",
                     json={"task_id": "node-1", "impl": "pausable",
                           "params": {"delay": 1.5}},
                     headers=auth_headers)
    _wait_status(hitl_client, run_id, "node-1", auth_headers, "running")
    resp = hitl_client.post(f"/runs/{run_id}/hitl/pause",
                            json={"task_id": "node-1"},
                            headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    # Dual receipt discipline: the execution receipt is polled for.
    receipt = _wait_receipt(hitl_client, run_id, body["action_id"],
                            auth_headers)
    assert receipt["action_id"] == body["action_id"]

    settled = _wait_terminal(hitl_client, run_id, "node-1", auth_headers)
    assert settled["status"] == "cancelled"
    gen1 = settled["execution_id"]

    resp = hitl_client.post(f"/runs/{run_id}/hitl/resume", json={},
                            headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    rerun = _wait_new_generation(hitl_client, run_id, "node-1", gen1,
                                 auth_headers)
    assert rerun["status"] == "succeeded"
    assert gen1 in rerun["previous_execution_ids"]


def test_pause_requires_task_id(hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    resp = hitl_client.post(f"/runs/{run_id}/hitl/pause", json={},
                            headers=auth_headers)
    assert resp.status_code == 422


def test_retry_terminal_node_reopens_new_generation(hitl_client,
                                                    auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    hitl_client.post(f"/runs/{run_id}/tasks",
                     json={"task_id": "node-1", "impl": "echo",
                           "params": {"marker": "v1"}},
                     headers=auth_headers)
    settled = _wait_terminal(hitl_client, run_id, "node-1", auth_headers)
    assert settled["status"] == "succeeded"
    gen1 = settled["execution_id"]

    resp = hitl_client.post(f"/runs/{run_id}/hitl/retry",
                            json={"task_id": "node-1"},
                            headers=auth_headers)
    assert resp.status_code == 200
    rerun = _wait_new_generation(hitl_client, run_id, "node-1", gen1,
                                 auth_headers)
    assert rerun["status"] == "succeeded"
    assert gen1 in rerun["previous_execution_ids"]


def test_retry_rejects_non_terminal_node(hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    hitl_client.post(f"/runs/{run_id}/tasks",
                     json={"task_id": "node-1", "impl": "pausable",
                           "params": {"delay": 1.0}},
                     headers=auth_headers)
    resp = hitl_client.post(f"/runs/{run_id}/hitl/retry",
                            json={"task_id": "node-1"},
                            headers=auth_headers)
    assert resp.status_code == 409
    assert "not terminal" in resp.json()["detail"]
    _wait_terminal(hitl_client, run_id, "node-1", auth_headers)


def test_retry_rejects_run_root(hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    resp = hitl_client.post(f"/runs/{run_id}/hitl/retry",
                            json={"task_id": run_id},
                            headers=auth_headers)
    assert resp.status_code == 422


def test_retry_rejects_unknown_descriptor(hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    resp = hitl_client.post(f"/runs/{run_id}/hitl/retry",
                            json={"task_id": "ghost"},
                            headers=auth_headers)
    assert resp.status_code == 409


def test_retry_blocked_by_active_descendant(hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    hitl_client.post(f"/runs/{run_id}/tasks",
                     json={"task_id": "parent", "impl": "echo",
                           "params": {}},
                     headers=auth_headers)
    hitl_client.post(f"/runs/{run_id}/tasks",
                     json={"task_id": "child", "impl": "pausable",
                           "params": {"delay": 1.5},
                           "upstream": ["parent"]},
                     headers=auth_headers)
    _wait_terminal(hitl_client, run_id, "parent", auth_headers)
    # Orphan guard keys on ACTIVE descendants: the child must be
    # running, not merely declared, before the retry is attempted.
    _wait_status(hitl_client, run_id, "child", auth_headers, "running")
    resp = hitl_client.post(f"/runs/{run_id}/hitl/retry",
                            json={"task_id": "parent"},
                            headers=auth_headers)
    assert resp.status_code == 409
    assert "child" in resp.json()["detail"]
    _wait_terminal(hitl_client, run_id, "child", auth_headers)


def test_receipt_pending_is_404(hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    resp = hitl_client.get(f"/runs/{run_id}/hitl/receipt/nope",
                           headers=auth_headers)
    assert resp.status_code == 404


def test_hitl_after_finish_is_404(hitl_client, auth_headers):
    run_id = _start_run(hitl_client, auth_headers)
    hitl_client.post(f"/runs/{run_id}/tasks",
                     json={"task_id": "node-1", "impl": "echo",
                           "params": {}},
                     headers=auth_headers)
    _wait_terminal(hitl_client, run_id, "node-1", auth_headers)
    resp = hitl_client.post(f"/runs/{run_id}/finish", headers=auth_headers)
    assert resp.status_code == 200
    resp = hitl_client.post(f"/runs/{run_id}/hitl/pause",
                            json={"task_id": "node-1"},
                            headers=auth_headers)
    assert resp.status_code == 404
    assert "not active" in resp.json()["detail"]