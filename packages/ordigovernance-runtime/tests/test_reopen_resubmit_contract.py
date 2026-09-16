"""Reopen+resubmit contract lock (application-layer semantics).

Locks the ordigovernance-side contract after the orditect v0.1.8
schedule-only fix: reopen_task advances the chain, and the resubmission
pattern used by the acceptance ground (reopen + submit without
if_not_exists) is the correct one. This suite runs on the in-memory
fixture (storage-layer semantics); the real-redis submit behavior is
pinned by orditect's own integration suite and by the real acceptance
run's hot-record assertions.
"""

from __future__ import annotations

import asyncio

from ordigovernance.testing.hot_path import MemoryTaskStorage


def _run(coro):
    return asyncio.run(coro)

def test_reopen_then_submit_pattern_keeps_chain():
    """The acceptance pattern: reopen (chain advances), submit never
    re-initializes a PENDING record on the fixed orchestrator."""
    storage = MemoryTaskStorage()
    _run(storage.initialize_task("node", initial_status="succeeded"))
    _run(storage.update_task("node", {"result": {"gen": 1}}))
    gen1 = _run(storage.get_task("node"))["execution_id"]

    _run(storage.reopen_task("node"))
    rec = _run(storage.get_task("node"))
    # The pattern preconditions the schedule-only path keys on:
    assert rec["status"] == "pending"
    assert rec["previous_execution_ids"] == [gen1]
    assert rec["previous_status"] == "succeeded"
    assert "result" not in rec

    # Reopen is terminal-only: the pending gen-2 must settle before a
    # third reopen is legal (mirrors the executor-driven state machine).
    _run(storage.update_task("node", {"status": "succeeded"}))
    _run(storage.reopen_task("node"))
    rec = _run(storage.get_task("node"))
    assert len(rec["previous_execution_ids"]) == 2


def test_reopen_clears_cancel_request_for_resubmit():
    """A HITL-paused node resumed via reopen must not carry the stale
    cancel flag into its new generation. The pause settles the node as
    cancelled (terminal), so the reopen is legal on the real contract."""
    storage = MemoryTaskStorage()
    _run(storage.initialize_task("node", initial_status="cancelled"))
    _run(storage.request_cancel("node"))
    _run(storage.reopen_task("node"))
    rec = _run(storage.get_task("node"))
    assert "cancel_requested" not in rec
    assert rec["previous_status"] == "cancelled"