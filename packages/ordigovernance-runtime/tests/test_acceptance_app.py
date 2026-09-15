"""Acceptance-ground smoke: the full workflow runs end to end in CI.

Mirrors the quickstart smoke gate: drives the acceptance run once and
asserts the terminal status, a non-empty audit stream, and the
two-generation beats (scope-retry reopen on researcher-1, HITL
pause/resume on researcher-2).
"""

from __future__ import annotations

import json

import pytest

from examples.acceptance.app import RESEARCHERS, execute_acceptance_run


@pytest.mark.asyncio
async def test_acceptance_run_settles_succeeded(tmp_path):
    record = await execute_acceptance_run(tmp_path / "trace")
    assert record["status"] == "succeeded"

    trace_dir = tmp_path / "trace"
    audit_lines = [
        json.loads(x) for x in
        (trace_dir / "audit.ndjson").read_text().splitlines()
        if x.strip()
    ]
    assert audit_lines, "audit stream must be non-empty"

    snapshot_lines = [
        json.loads(x) for x in
        (trace_dir / "snapshots.ndjson").read_text().splitlines()
        if x.strip()
    ]

    def _rows(task_id):
        return [
            s.get("data", s) for s in snapshot_lines
            if s.get("data", s).get("task_id") == task_id
        ]

    r1_eids = {r.get("execution_id") for r in _rows(RESEARCHERS[0])}
    assert len(r1_eids) == 2, \
        "reopened researcher-1 must carry two generations"

    r2_rows = _rows(RESEARCHERS[1])
    r2_eids = {r.get("execution_id") for r in r2_rows}
    assert len(r2_eids) == 2, \
        "paused/resumed researcher-2 must carry two generations"
    assert "cancelled" in {r.get("status") for r in r2_rows}, \
        "the HITL pause beat must settle one cancelled generation"