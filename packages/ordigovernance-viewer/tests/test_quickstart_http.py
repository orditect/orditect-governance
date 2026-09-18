"""Quickstart HTTP surface: the demo app answers over TestClient.

Regression cover for the generation-content route wiring: the router
speaks the MemoBackend protocol, and the mock memory body must be
wrapped by the handler adapter -- a bare module lacks the keyword
surface and every read used to TypeError. The demo's HTTP surface now
has TestClient coverage, not only execution coverage.

Requires the viewer dev + quickstart extras.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ordigovernance.viewer.examples.quickstart import (
    RESEARCHERS,
    _build_hot_path,
    _build_viewer_app,
    _execute,
    _hot,
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    trace_dir = tmp_path_factory.mktemp("quickstart-http") / "trace"
    hot = _build_hot_path()
    _hot.clear()
    _hot.update(hot)
    import asyncio

    asyncio.run(_execute(hot, trace_dir))
    with TestClient(_build_viewer_app(trace_dir)) as c:
        yield c


def test_generations_list_carries_two_generations_for_reopened(client):
    resp = client.get("/api/runs/quickstart/generations",
                      params={"root_id": "quickstart-root"})
    assert resp.status_code == 200
    rows = resp.json()
    eids = {r.get("execution_id") for r in rows
            if r.get("task_id") == RESEARCHERS[0]}
    assert len(eids) == 2


def test_generation_content_reads_through_adapter(client):
    resp = client.get("/api/runs/quickstart/generations",
                      params={"root_id": "quickstart-root"})
    row = next(r for r in resp.json()
               if r.get("task_id") == RESEARCHERS[0]
               and r.get("status") == "succeeded")
    content = client.get(
        f"/api/runs/quickstart/generations/{row['task_id']}/"
        f"{row['execution_id']}/content")
    assert content.status_code == 200
    body = content.json()
    assert body["result"]["topic"] == RESEARCHERS[0]


def test_audit_and_validate_endpoints(client):
    audit = client.get("/api/runs/quickstart/audit")
    assert audit.status_code == 200
    assert audit.json(), "audit stream must be non-empty"
    val = client.get("/api/runs/quickstart/validate",
                     params={"root_id": "quickstart-root"})
    assert val.status_code == 200
    assert val.json()["available"] is True