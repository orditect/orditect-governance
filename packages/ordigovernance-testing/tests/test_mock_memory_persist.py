"""Mock memory body persistence: multi-process merge semantics.

Locks the read-merge-write contract: a process holding a stale (or
empty) in-memory snapshot must never erase keys written by other
processes since its snapshot was taken. Regression cover for the
CLI-run archives erased by an app-process curate dump.
"""

from __future__ import annotations

import json

import pytest

from ordigovernance.testing import mock_tools


@pytest.fixture
def fresh_body(tmp_path, monkeypatch):
    """Reset the module globals and point persistence at a tmp file."""
    path = tmp_path / "mock_memory.json"
    mock_tools._MEMORY.clear()
    mock_tools.configure_memory_body(path)
    yield path
    mock_tools._MEMORY.clear()
    mock_tools.configure_memory_body(None)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_write_persists_and_merges_with_foreign_keys(fresh_body):
    path = fresh_body
    # Another process's pre-existing content.
    path.write_text(json.dumps({
        "gen-result/analyst-pricing/exec-run1": {"result": {"a": 1},
                                                 "input_pins": {}},
        "memo/scope-run1/vector/1/abc": {"result": {"v": 1}},
    }))
    # This process's stale snapshot is EMPTY (it never loaded the
    # file); its write must merge, not overwrite.
    _run(mock_tools.memory_write("memo/scope-app/x/1/def", {"result": 2}))

    on_disk = json.loads(path.read_text())
    assert "gen-result/analyst-pricing/exec-run1" in on_disk
    assert "memo/scope-run1/vector/1/abc" in on_disk
    assert "memo/scope-app/x/1/def" in on_disk
    # The in-memory snapshot is refreshed to the merged state.
    assert "gen-result/analyst-pricing/exec-run1" in mock_tools._MEMORY


def test_same_key_uses_this_process_version(fresh_body):
    path = fresh_body
    path.write_text(json.dumps({"k": {"old": True}}))
    _run(mock_tools.memory_write("k", {"new": True}))
    on_disk = json.loads(path.read_text())
    assert on_disk["k"] == {"new": True}


def test_corrupt_file_degrades_to_own_writes(fresh_body):
    path = fresh_body
    path.write_text("{not json")
    _run(mock_tools.memory_write("k", {"v": 1}))
    on_disk = json.loads(path.read_text())
    assert on_disk == {"k": {"v": 1}}


def test_two_process_interleaving_preserves_both(fresh_body):
    """Simulate two processes: process A writes, process B (stale
    snapshot) writes afterwards; A's keys must survive."""
    path = fresh_body
    # Process A writes its archive.
    _run(mock_tools.memory_write(
        "gen-result/supervisor/exec-a", {"result": {}, "input_pins": {}}))

    # Process B boots from the current file, then A writes MORE, then
    # B writes: B's dump must carry A's later keys too.
    mock_tools._MEMORY.clear()
    mock_tools.configure_memory_body(path)      # B loads A's first key
    _run(mock_tools.memory_write(
        "gen-result/publisher/exec-a2",         # A writes again later
        {"result": {}, "input_pins": {}}))
    # Simulate B being stale: drop A's second key from "B's" memory.
    mock_tools._MEMORY.pop("gen-result/publisher/exec-a2", None)
    _run(mock_tools.memory_write("memo/scope-b/x/1/abc", {"result": 1}))

    on_disk = json.loads(path.read_text())
    assert "gen-result/supervisor/exec-a" in on_disk
    assert "gen-result/publisher/exec-a2" in on_disk
    assert "memo/scope-b/x/1/abc" in on_disk