"""Hot-path fake semantics: mirror the production TaskRedisDB contract.

Locked by scripts/probe_reopen_semantics.py against real redis:
  - reopen_task preserves the generation chain, writes
    previous_status, clears result and cancel flags;
  - initialize_task on an EXISTING record (if_not_exists=False)
    resets the chain unconditionally.
"""

from __future__ import annotations

import asyncio

from ordigovernance.testing.hot_path import MemoryTaskStorage


def _run(coro):
    return asyncio.run(coro)


def test_reopen_preserves_chain_and_clears_state():
    storage = MemoryTaskStorage()
    _run(storage.initialize_task("t", initial_status="succeeded"))
    _run(storage.update_task("t", {"result": {"gen": 1}}))
    gen1 = _run(storage.get_task("t"))["execution_id"]

    _run(storage.reopen_task("t"))
    rec = _run(storage.get_task("t"))
    assert rec["previous_execution_ids"] == [gen1]
    assert rec["previous_status"] == "succeeded"
    assert rec["status"] == "pending"
    assert "result" not in rec
    assert rec["execution_id"] != gen1

def test_initialize_existing_record_resets_chain():
    """if_not_exists=False on an existing record: full reset (production parity)."""
    storage = MemoryTaskStorage()
    _run(storage.initialize_task("t", initial_status="succeeded"))
    _run(storage.reopen_task("t"))

    created = _run(storage.initialize_task("t", initial_status="pending"))
    rec = _run(storage.get_task("t"))
    assert created is True
    assert rec["previous_execution_ids"] == []


def test_initialize_if_not_exists_skips_existing():
    storage = MemoryTaskStorage()
    _run(storage.initialize_task("t", initial_status="succeeded"))
    _run(storage.reopen_task("t"))
    prevs = _run(storage.get_task("t"))["previous_execution_ids"]

    created = _run(storage.initialize_task(
        "t", initial_status="pending", if_not_exists=True))
    rec = _run(storage.get_task("t"))
    assert created is False
    assert rec["previous_execution_ids"] == prevs