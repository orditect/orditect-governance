"""Acceptance-ground smoke: the full workflow runs end to end in CI.

Mirrors the quickstart smoke gate: drives the acceptance run once and
asserts the terminal status, a non-empty audit stream, and the
two-generation beat on the reopened researcher.
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
    eids = {
        s.get("data", s).get("execution_id")
        for s in snapshot_lines
        if s.get("data", s).get("task_id") == RESEARCHERS[0]
    }
    assert len(eids) == 2, "reopened researcher must carry two generations"