"""Orphan descendant guard (acceptance)."""

from __future__ import annotations

import pytest

from ordigovernance.runtime.archive.archive import archive_generation
from ordigovernance.runtime.orchestration.orphan_guard import (
    ActiveDescendantsError,
    assert_no_active_descendants,
    find_active_descendants,
    find_active_pin_consumers,
)


class _FakeDepsReader:
    def __init__(self, edges):
        self._edges = edges

    async def read_graph(self, root_id):
        return {"task_ids": [], "edges": list(self._edges)}


class _FakeTaskIO:
    def __init__(self, statuses=None):
        self._statuses = dict(statuses or {})
        self._eids: dict = {}

    async def get_task(self, task_id):
        status = self._statuses.get(task_id)
        if status is None:
            return {}
        record = {"status": status}
        record["execution_id"] = self._eids.get(
            task_id, f"exec-{task_id}-g1")
        return record

    async def update_task(self, task_id, patch):
        raise NotImplementedError


def _edge(child, parent):
    return {"child_id": child, "parent_id": parent, "is_primary": True}


class TestFindActiveDescendants:
    @pytest.mark.asyncio
    async def test_no_descendants_passes(self):
        deps = _FakeDepsReader([])
        io = _FakeTaskIO({"sup": "running"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == []

    @pytest.mark.asyncio
    async def test_terminal_descendants_pass(self):
        deps = _FakeDepsReader([_edge("a", "sup"), _edge("b", "sup")])
        io = _FakeTaskIO({"a": "succeeded", "b": "failed"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == []

    @pytest.mark.asyncio
    async def test_running_descendant_detected(self):
        deps = _FakeDepsReader([_edge("a", "sup"), _edge("b", "sup")])
        io = _FakeTaskIO({"a": "succeeded", "b": "running"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == ["b"]

    @pytest.mark.asyncio
    async def test_transitive_descendants_detected(self):
        deps = _FakeDepsReader([_edge("a", "sup"), _edge("b", "a")])
        io = _FakeTaskIO({"a": "succeeded", "b": "running"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == ["b"]

    @pytest.mark.asyncio
    async def test_multiple_active_reported(self):
        deps = _FakeDepsReader([_edge("a", "sup"), _edge("b", "sup"),
                                _edge("c", "a")])
        io = _FakeTaskIO({"a": "running", "b": "running",
                          "c": "running"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_missing_hot_record_is_not_active(self):
        deps = _FakeDepsReader([_edge("a", "sup")])
        io = _FakeTaskIO({})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == []

    @pytest.mark.asyncio
    async def test_cycle_does_not_hang(self):
        deps = _FakeDepsReader([_edge("a", "sup"), _edge("sup", "a")])
        io = _FakeTaskIO({"a": "succeeded"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == []

    @pytest.mark.asyncio
    async def test_object_shaped_edges(self):
        class _E:
            def __init__(self, child, parent):
                self.child_id = child
                self.parent_id = parent

        deps = _FakeDepsReader([_E("a", "sup")])
        io = _FakeTaskIO({"a": "running"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == ["a"]

    @pytest.mark.asyncio
    async def test_empty_graph_passes(self):
        class _EmptyDeps:
            async def read_graph(self, root_id):
                return None

        io = _FakeTaskIO({"sup": "running"})
        assert await find_active_descendants(
            "sup", deps_reader=_EmptyDeps(), task_io=io) == []

    @pytest.mark.asyncio
    async def test_initialized_but_never_submitted_is_not_active(self):
        class _PendingOnlyIO:
            async def get_task(self, task_id):
                return {"status": "pending"}  # no execution_id

            async def update_task(self, task_id, patch):
                raise NotImplementedError

        deps = _FakeDepsReader([_edge("writer", "sup"),
                                _edge("review", "sup")])
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=_PendingOnlyIO()) == []

    @pytest.mark.asyncio
    async def test_pending_descendant_does_not_block(self):
        deps = _FakeDepsReader([_edge("writer", "sup"),
                                _edge("review", "sup")])
        io = _FakeTaskIO({"writer": "pending", "review": "pending"})
        assert await find_active_descendants(
            "sup", deps_reader=deps, task_io=io) == []


class TestAssertNoActiveDescendants:
    @pytest.mark.asyncio
    async def test_raises_with_full_active_list(self):
        deps = _FakeDepsReader([_edge("a", "sup"), _edge("b", "sup")])
        io = _FakeTaskIO({"a": "running", "b": "running"})
        with pytest.raises(ActiveDescendantsError) as exc_info:
            await assert_no_active_descendants(
                "sup", deps_reader=deps, task_io=io)
        err = exc_info.value
        assert err.task_id == "sup"
        assert err.active == ("a", "b")
        assert "a" in str(err) and "b" in str(err)

    @pytest.mark.asyncio
    async def test_passes_when_all_terminal(self):
        deps = _FakeDepsReader([_edge("a", "sup")])
        io = _FakeTaskIO({"a": "succeeded"})
        await assert_no_active_descendants(
            "sup", deps_reader=deps, task_io=io)  # no raise


class _FakeBackend:
    def __init__(self):
        self.store: dict[str, dict] = {}

    async def memory_read(self, key, *, call_id, payload_fn=None):
        return {"key": key, "value": self.store.get(key)}

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.store[key] = value
        return {"key": key, "stored": True}


class _EidTaskIO:
    """TaskIO double carrying execution ids (the pin check needs them)."""

    def __init__(self, inner: _FakeTaskIO):
        self._inner = inner

    async def get_task(self, task_id):
        record = await self._inner.get_task(task_id)
        eid = self._inner._eids.get(task_id)
        if eid is not None:
            record = dict(record)
            record["execution_id"] = eid
        return record

    async def update_task(self, task_id, patch):
        raise NotImplementedError


class TestFindActivePinConsumers:
    @pytest.mark.asyncio
    async def test_active_consumer_detected(self):
        backend = _FakeBackend()
        await archive_generation(backend, task_id="reader", eid="e-r1",
                                 result={}, pins={"sup": "e-s1"})
        io = _FakeTaskIO({"sup": "succeeded", "reader": "running"})
        io._eids = {"sup": "e-s1", "reader": "e-r1"}
        active = await find_active_pin_consumers(
            "sup", backend=backend, task_io=_EidTaskIO(io))
        assert active == [{"task_id": "reader", "eid": "e-r1"}]

    @pytest.mark.asyncio
    async def test_terminal_consumer_ignored(self):
        backend = _FakeBackend()
        await archive_generation(backend, task_id="reader", eid="e-r1",
                                 result={}, pins={"sup": "e-s1"})
        io = _FakeTaskIO({"sup": "succeeded", "reader": "succeeded"})
        io._eids = {"sup": "e-s1", "reader": "e-r1"}
        active = await find_active_pin_consumers(
            "sup", backend=backend, task_io=_EidTaskIO(io))
        assert active == []

    @pytest.mark.asyncio
    async def test_moved_on_consumer_is_terminal_by_construction(self):
        backend = _FakeBackend()
        await archive_generation(backend, task_id="reader", eid="e-r1",
                                 result={}, pins={"sup": "e-s1"})
        io = _FakeTaskIO({"sup": "succeeded", "reader": "running"})
        io._eids = {"sup": "e-s1", "reader": "e-r2"}  # moved on
        active = await find_active_pin_consumers(
            "sup", backend=backend, task_io=_EidTaskIO(io))
        assert active == []

    @pytest.mark.asyncio
    async def test_missing_index_entry_degrades_to_empty(self):
        backend = _FakeBackend()
        io = _FakeTaskIO({"sup": "succeeded"})
        io._eids = {"sup": "e-s1"}
        active = await find_active_pin_consumers(
            "sup", backend=backend, task_io=_EidTaskIO(io))
        assert active == []

    @pytest.mark.asyncio
    async def test_no_backend_degrades_to_empty(self):
        io = _FakeTaskIO({"sup": "succeeded"})
        io._eids = {"sup": "e-s1"}
        active = await find_active_pin_consumers(
            "sup", backend=None, task_io=_EidTaskIO(io))
        assert active == []