
"""In-process M3 rehearsal: the n8n demo registry over HTTP.

Drives the full demo narrative (researcher fan-out -> writer ->
reviewer -> publisher) through the gateway's task plane with the
reference registry injected and scripted clients on the D12 seam,
then asserts the evidence shapes the compose walkthrough verifies
against the viewer: generations, archive saves at the archive band,
dependency edges, and pins. The reviewer needs a SCORE line, so the
scripted client appends one whenever the prompt asks for it.

Run from the repository root (examples/ resolves from there).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from examples.gateway_n8n.registry import build_registry
from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.config import GatewaySettings
from ordigovernance.testing.mock_llm import ScriptedLLMClient

RESEARCHERS = ("ev-battery", "ev-charging", "ev-supply")


class _DemoScriptedLLM(ScriptedLLMClient):
    """Scripted client that answers review prompts with a SCORE line."""

    async def chat(self, messages, *, call_id, **kwargs):
        resp = await super().chat(messages, call_id=call_id, **kwargs)
        if "SCORE:" in repr(messages):
            resp["choices"][0]["message"]["content"] += "\nSCORE: 88"
        return resp


async def _scripted_clients(hooks: dict) -> dict:
    """D12 seam: the demo's three clients over the governed call plane."""

    class _GovernedScriptedLLM:
        def __init__(self, governor, budget, store, *, resource: str):
            from orditect.flow import GovernedCallClient

            self._scripted = _DemoScriptedLLM()
            self._client = GovernedCallClient(
                governor,
                resource=resource,
                handler=self._handler,
                budget=budget,
                cost_fn=lambda result: (
                    (result.get("usage") or {}).get("total_tokens", 1)
                    if isinstance(result, dict) else 1),
                audit_writer=store.audit,
                content_writer=store.content,
                event_type="llm_call",
                task_id="gateway-demo",
            )

        async def _handler(self, messages, **kwargs):
            return await self._scripted.chat(messages, call_id="internal",
                                             **kwargs)

        async def chat(self, messages, *, call_id, **kwargs):
            return await self._client.call(
                messages, call_id=call_id, **kwargs)

    return {
        "research": _GovernedScriptedLLM(
            hooks["governor"], hooks["budget"], hooks["store"],
            resource="llm_research"),
        "writing": _GovernedScriptedLLM(
            hooks["governor"], hooks["budget"], hooks["store"],
            resource="llm_writing"),
        "publish": _GovernedScriptedLLM(
            hooks["governor"], hooks["budget"], hooks["store"],
            resource="llm_writing"),
    }


@pytest.fixture
def demo_settings(tmp_path: Path) -> GatewaySettings:
    return GatewaySettings(
        redis_url=None,
        trace_root=tmp_path / "runs",
        auth_token="test-token",
        semaphores={"task_execution": 8, "llm_research": 2,
                    "llm_writing": 1, "web_search": 2, "memo_store": 2},
        make_clients=_scripted_clients,
    )


@pytest.fixture
def demo_client(demo_settings):
    app = build_app(demo_settings, registry=build_registry())
    with TestClient(app) as c:
        yield c


def _start_run(client, headers, run_id):
    resp = client.post("/runs", json={"run_id": run_id}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["run_id"]


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


def _read_event_ids(trace_dir: Path) -> list[str]:
    path = trace_dir / "audit.ndjson"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out.append(row.get("data", row).get("event_id", ""))
    return out


def _read_edges(trace_dir: Path) -> set[tuple[str, str]]:
    path = trace_dir / "deps.ndjson"
    if not path.is_file():
        return set()
    edges = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        data = data.get("data", data)
        edges.add((data.get("child_id"), data.get("parent_id")))
    return edges


def test_demo_narrative_end_to_end(demo_client, auth_headers,
                                   demo_settings):
    run_id = _start_run(demo_client, auth_headers, "demo-e2e")

    for topic in RESEARCHERS:
        resp = demo_client.post(
            f"/runs/{run_id}/tasks",
            json={"task_id": topic, "impl": "researcher",
                  "params": {"topic": topic}},
            headers=auth_headers)
        assert resp.status_code == 201
    for topic in RESEARCHERS:
        settled = _wait_terminal(demo_client, run_id, topic, auth_headers)
        assert settled["status"] == "succeeded"

    resp = demo_client.post(
        f"/runs/{run_id}/tasks",
        json={"task_id": "writer", "impl": "writer",
              "params": {"upstream": list(RESEARCHERS)},
              "upstream": list(RESEARCHERS)},
        headers=auth_headers)
    assert resp.status_code == 201
    writer = _wait_terminal(demo_client, run_id, "writer", auth_headers)
    assert writer["status"] == "succeeded"
    assert set(writer["result"]["input_pins"]) == set(RESEARCHERS)

    resp = demo_client.post(
        f"/runs/{run_id}/tasks",
        json={"task_id": "reviewer", "impl": "reviewer",
              "params": {"producer_id": "writer"},
              "upstream": ["writer"]},
        headers=auth_headers)
    assert resp.status_code == 201
    reviewer = _wait_terminal(demo_client, run_id, "reviewer",
                              auth_headers)
    assert reviewer["status"] == "succeeded"
    assert reviewer["result"]["score"] == 88
    assert reviewer["result"]["input_pins"]["writer"] == \
        writer["execution_id"]

    resp = demo_client.post(
        f"/runs/{run_id}/tasks",
        json={"task_id": "publisher", "impl": "publisher",
              "params": {"producer_id": "writer"},
              "upstream": ["writer"]},
        headers=auth_headers)
    assert resp.status_code == 201
    publisher = _wait_terminal(demo_client, run_id, "publisher",
                               auth_headers)
    assert publisher["status"] == "succeeded"
    assert publisher["result"]["input_pins"] == \
        {"writer": writer["execution_id"]}

    resp = demo_client.post(f"/runs/{run_id}/finish",
                            headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["final_status"] == "succeeded"

    # Evidence shapes: every generation archived at the archive band,
    # and the declared edges match the narrative structure.
    trace_dir = demo_settings.trace_root / run_id / "trace"
    event_ids = _read_event_ids(trace_dir)
    for tid in (*RESEARCHERS, "writer", "reviewer", "publisher"):
        assert any(e.startswith(f"memsave-{tid}-") and e.endswith("-90")
                   for e in event_ids), tid
    edges = _read_edges(trace_dir)
    for topic in RESEARCHERS:
        assert ("writer", topic) in edges
    assert ("reviewer", "writer") in edges
    assert ("publisher", "writer") in edges


def test_demo_quality_gate_pair_composite(demo_client, auth_headers,
                                          demo_settings):
    run_id = _start_run(demo_client, auth_headers, "demo-comp")

    demo_client.post(f"/runs/{run_id}/tasks",
                     json={"task_id": "ev-battery", "impl": "researcher",
                           "params": {"topic": "ev-battery"}},
                     headers=auth_headers)
    settled = _wait_terminal(demo_client, run_id, "ev-battery",
                             auth_headers)
    assert settled["status"] == "succeeded"

    resp = demo_client.post(
        f"/runs/{run_id}/composites",
        json={"name": "quality_gate_pair",
              "params": {"producer_id": "writer", "judge_id": "reviewer",
                         "producer_impl": "writer",
                         "judge_impl": "reviewer",
                         "producer_params": {"upstream": ["ev-battery"]},
                         "upstream": ["ev-battery"], "threshold": 80}},
        headers=auth_headers)
    assert resp.status_code == 202
    composite_id = resp.json()["composite_id"]

    body = _wait_composite(demo_client, run_id, composite_id,
                           auth_headers)
    assert body["status"] == "succeeded"
    assert body["outcome"]["passed"] is True
    assert body["outcome"]["scores"] == [88]
    assert [c["task_id"] for c in body["children"]] == \
        ["writer", "reviewer"]

    edges = _read_edges(demo_settings.trace_root / run_id / "trace")
    assert ("writer", "ev-battery") in edges
    assert ("reviewer", "writer") in edges


def test_demo_vocabulary_lists_reference_names(demo_client, auth_headers):
    resp = demo_client.get("/runs/ambient/vocabulary",
                           headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert {e["name"] for e in body["impls"]} == \
        {"researcher", "writer", "reviewer", "publisher",
         "slow_researcher"}
    assert {e["name"] for e in body["tools"]} == {"search"}
    assert {e["name"] for e in body["composites"]} == \
        {"quality_gate_pair"}