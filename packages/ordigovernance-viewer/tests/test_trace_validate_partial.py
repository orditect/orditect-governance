"""Trace validate endpoint: tolerates a partially-written tail line."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ordigovernance.viewer.api.trace import build_trace_router


def _app(trace_root):
    app = FastAPI()
    app.include_router(build_trace_router(
        lambda run_id: None,
        resolve_trace_dir=lambda run_id: trace_root / run_id,
    ))
    return app


def test_validate_tolerates_partial_tail(tmp_path):
    trace_dir = tmp_path / "r1"
    trace_dir.mkdir()
    (trace_dir / "snapshots.ndjson").write_text(
        '{"data": {"task_id": "a", "status": "running"}}\n'
        '{"data": {"task_id": "b", "status": "pend'
    )
    (trace_dir / "audit.ndjson").write_text("")

    client = TestClient(_app(tmp_path))
    resp = client.get("/api/runs/r1/validate")
    assert resp.status_code == 200
    assert resp.json()["available"] is True


def test_validate_empty_bundle_reports_unavailable(tmp_path):
    (tmp_path / "r1").mkdir()
    client = TestClient(_app(tmp_path))
    resp = client.get("/api/runs/r1/validate")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_validate_without_reconcile_fn_skips_pin_findings(tmp_path):
    """No reconcile_fn injected: run_rules findings only, no DR-PIN rows."""
    trace_dir = tmp_path / "r1"
    trace_dir.mkdir()
    (trace_dir / "snapshots.ndjson").write_text(
        json.dumps({"data": {"task_id": "w", "execution_id": "e-w1",
                             "status": "succeeded"}}) + "\n"
    )
    (trace_dir / "audit.ndjson").write_text("")
    client = TestClient(_app(tmp_path))
    resp = client.get("/api/runs/r1/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert not [f for f in body["findings"]
                if str(f["rule"]).startswith("DR-PIN-")]