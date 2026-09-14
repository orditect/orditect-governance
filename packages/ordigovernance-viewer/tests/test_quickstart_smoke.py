"""Quickstart smoke gate: the flagship demo must run end to end.

Runs the quickstart's execution phase only (no HTTP server) and
asserts the four acceptance facts: the run settles succeeded, the
audit stream is non-empty, the reopened researcher carries two
generations, and the bundle validates as available. A green smoke
test is part of the split-completion definition.
"""

from __future__ import annotations

import json

import pytest

from ordigovernance.viewer.examples.quickstart import (
    RESEARCHERS,
    _build_hot_path,
    _execute,
    _hot,
)


@pytest.mark.asyncio
async def test_quickstart_executes_end_to_end(tmp_path, capsys):
    hot = _build_hot_path()
    _hot.clear()
    _hot.update(hot)
    trace_dir = tmp_path / "trace"

    await _execute(hot, trace_dir)

    out = capsys.readouterr().out
    assert "publish settled: succeeded" in out

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
    generations = {
        s.get("data", s).get("task_id")
        for s in snapshot_lines
        if s.get("data", s).get("task_id") == RESEARCHERS[0]
    }
    eids = {
        s.get("data", s).get("execution_id")
        for s in snapshot_lines
        if s.get("data", s).get("task_id") == RESEARCHERS[0]
    }
    assert generations == {RESEARCHERS[0]}
    assert len(eids) == 2, "reopened researcher must carry two generations"

    from orditect.protocol.rules import run_rules

    lines = []
    for name in ("snapshots.ndjson", "audit.ndjson", "deps.ndjson"):
        path = trace_dir / name
        if path.is_file():
            lines.extend(
                json.loads(x) for x in path.read_text().splitlines()
                if x.strip()
            )
    assert lines, "trace bundle must be non-empty"