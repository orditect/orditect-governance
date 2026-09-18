"""SessionManager: ambient run, single-active guard, call identity."""

from __future__ import annotations

import pytest

from ordigovernance.api.naming import SEQ_AGENT_BASE
from ordigovernance.gateway.registry import GatewayRegistry
from ordigovernance.gateway.session import (
    AMBIENT_RUN_ID,
    SessionManager,
    check_reserved_payload_keys,
)


async def _startup(settings) -> SessionManager:
    manager = SessionManager(settings)
    await manager.startup(GatewayRegistry())
    return manager


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_ambient_exists_and_is_not_the_active_run(settings):
    manager = _run(_startup(settings))
    try:
        assert manager.ambient is not None
        assert manager.active is None
        # No run_id routes to ambient (D2).
        assert manager.resolve(None) is manager.ambient
        assert manager.resolve(AMBIENT_RUN_ID) is manager.ambient
        with pytest.raises(KeyError):
            manager.resolve("nope")
    finally:
        _run(manager.shutdown())


def test_single_active_run_guard(settings):
    manager = _run(_startup(settings))
    try:
        first = _run(manager.start_user_run("run-1"))
        assert first is not None
        assert manager.resolve("run-1") is first
        assert _run(manager.start_user_run("run-2")) is None
    finally:
        _run(manager.shutdown())


def test_call_identity_for_unknown_task_is_key_error(settings):
    manager = _run(_startup(settings))
    try:
        session = manager.ambient
        with pytest.raises(KeyError):
            _run(session.allocate_call_identity("ghost", "p"))
    finally:
        _run(manager.shutdown())


def test_ambient_ownership_is_catch_all_but_existing_only(settings):
    """Ambient attributes run-less traffic to any existing record.

    That is the one documented ownership exemption (D2); a task_id
    with no hot record is a 404 there too, so the catch-all never
    fabricates identities.
    """
    manager = _run(_startup(settings))
    try:
        user_run = _run(manager.start_user_run("run-1"))
        try:
            record = _run(manager.hot["storage"].get_task(
                user_run.root_id))
            assert record
            ambient = manager.ambient
            # Another run's existing record: attributed (D2 catch-all).
            _, eid, _ = _run(ambient.allocate_call_identity(
                user_run.root_id, "p"))
            assert eid == record["execution_id"]
            # A task id with no hot record anywhere: still a 404.
            with pytest.raises(KeyError):
                _run(ambient.allocate_call_identity("ghost-nowhere",
                                                    "p"))
        finally:
            _run(manager.finish_user_run("run-1", "succeeded"))
    finally:
        _run(manager.shutdown())


def test_call_identity_mints_ephemeral_and_monotonic_seqs(settings):
    manager = _run(_startup(settings))
    try:
        session = manager.ambient
        t1, e1, s1 = _run(session.allocate_call_identity(None, "p"))
        t2, e2, s2 = _run(session.allocate_call_identity(None, "p"))
        assert t1.startswith("n8n-call-") and t2.startswith("n8n-call-")
        # Ephemeral identities are unique per call; each one starts its
        # own (task_id, purpose) counter at the agent band base (D8).
        assert t1 != t2
        assert e1 != e2
        assert s1 == s2 == SEQ_AGENT_BASE + 1
    finally:
        _run(manager.shutdown())

def test_call_identity_for_existing_task_reuses_eid_and_increments(
        settings):
    manager = _run(_startup(settings))
    try:
        session = manager.ambient
        _run(manager.hot["storage"].initialize_task("t1"))
        record = _run(manager.hot["storage"].get_task("t1"))
        eid = record["execution_id"]
        t, e1, s1 = _run(session.allocate_call_identity("t1", "p"))
        _, e2, s2 = _run(session.allocate_call_identity("t1", "p"))
        # Same hot record -> same current eid; seqs monotonic per
        # (task_id, purpose).
        assert t == "t1"
        assert e1 == e2 == eid
        assert s2 == s1 + 1
        # A different purpose starts its own counter.
        _, _, s3 = _run(session.allocate_call_identity("t1", "other"))
        assert s3 == SEQ_AGENT_BASE + 1
    finally:
        _run(manager.shutdown())

def test_semaphore_table_check_rejects_missing_resources(settings):
    settings.semaphores.pop("memo_store")
    with pytest.raises(ValueError, match="semaphore table"):
        _run(_startup(settings))


def test_reserved_payload_keys_check():
    check_reserved_payload_keys("search", {"q": "ok"})
    with pytest.raises(ValueError, match="collide"):
        check_reserved_payload_keys("search", {"params": {"q": "x"}})