"""Trace validate endpoint: reconcile_fn injection is a display adapter.

Locks the open tier's side of the pin-reconciliation injection point:
the router maps engine findings to DR-PIN-* warning rows and never
invents attribution itself; reconcile failures degrade (skipped),
never fail the validation read; the no-reconcile_fn case is covered
by test_trace_validate_partial.py.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ordigovernance.viewer.api.trace import build_trace_router

_SENTINEL_BACKEND = object()


class _Finding:
    def __init__(self, kind, target, declared, actual):
        self.kind = kind
        self.target_task_id = target
        self.declared_eid = declared
        self.actual_eid = actual


class _Report:
    def __init__(self, available, findings=()):
        self.available = available
        self.findings = findings


class _SpyReconcile:
    def __init__(self, report=None, error=None):
        self._report = report or _Report(True, ())
        self._error = error
        self.calls: list[dict] = []

    async def __call__(self, backend, audit_events, *, task_id, eid):
        self.calls.append({"backend": backend,
                           "line_count": len(audit_events),
                           "task_id": task_id, "eid": eid})
        if self._error is not None:
            raise self._error
        return self._report


def _app(trace_root, reconcile_fn):
    app = FastAPI()
    app.include_router(build_trace_router(
        lambda run_id: None,
        resolve_trace_dir=lambda run_id: trace_root / run_id,
        resolve_archive_backend=lambda run_id: _SENTINEL_BACKEND,
        reconcile_fn=reconcile_fn,
    ))
    return app


def _bundle(tmp_path):
    trace_dir = tmp_path / "r1"
    trace_dir.mkdir()
    (trace_dir / "snapshots.ndjson").write_text(
        json.dumps({"data": {"task_id": "w", "execution_id": "e-w1",
                             "status": "succeeded"}}) + "\n")
    (trace_dir / "audit.ndjson").write_text("")
    return trace_dir


def _dr_pin_findings(body):
    return [f for f in body["findings"]
            if str(f["rule"]).startswith("DR-PIN-")]


def test_findings_map_to_dr_pin_warnings(tmp_path):
    _bundle(tmp_path)
    spy = _SpyReconcile(_Report(True, (
        _Finding("eid_mismatch", "a", "e-a1", "e-a2"),
        _Finding("undeclared_load", "b", None, "e-b1"),
        _Finding("unread_pin", "c", "e-c1", None),
    )))
    client = TestClient(_app(tmp_path, spy))
    resp = client.get("/api/runs/r1/validate")
    assert resp.status_code == 200
    body = resp.json()
    pins = _dr_pin_findings(body)
    assert [f["rule"] for f in pins] == [
        "DR-PIN-EID_MISMATCH",
        "DR-PIN-UNDECLARED_LOAD",
        "DR-PIN-UNREAD_PIN",
    ]
    assert all(f["severity"] == "warning" for f in pins)
    assert all(f["location"] == "w@e-w1" for f in pins)
    assert "e-a1" in pins[0]["message"] and "e-a2" in pins[0]["message"]
    assert "e-b1" in pins[1]["message"]
    assert "e-c1" in pins[2]["message"]
    assert body["warnings"] >= len(pins)
    # The spy received the resolved backend and the bundle lines.
    assert spy.calls == [{"backend": _SENTINEL_BACKEND,
                          "line_count": 1, "task_id": "w", "eid": "e-w1"}]


def test_unknown_kind_uses_generic_wording(tmp_path):
    _bundle(tmp_path)
    spy = _SpyReconcile(_Report(True, (
        _Finding("future_kind", "a", "e-a1", None),
    )))
    client = TestClient(_app(tmp_path, spy))
    body = client.get("/api/runs/r1/validate").json()
    pins = _dr_pin_findings(body)
    assert [f["rule"] for f in pins] == ["DR-PIN-FUTURE_KIND"]
    assert "never archive-read" in pins[0]["message"]


def test_reconcile_failure_degrades_to_no_findings(tmp_path):
    _bundle(tmp_path)
    spy = _SpyReconcile(error=RuntimeError("backend down"))
    client = TestClient(_app(tmp_path, spy))
    resp = client.get("/api/runs/r1/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert _dr_pin_findings(body) == []


def test_unavailable_report_emits_no_findings(tmp_path):
    _bundle(tmp_path)
    spy = _SpyReconcile(_Report(False, ()))
    client = TestClient(_app(tmp_path, spy))
    body = client.get("/api/runs/r1/validate").json()
    assert _dr_pin_findings(body) == []