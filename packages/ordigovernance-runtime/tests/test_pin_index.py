"""Pinned-by reverse index (acceptance)."""

from __future__ import annotations

import asyncio

import pytest

from ordigovernance.runtime.archive.archive import (
    _INDEX_CONSUMER_CAP,
    archive_generation,
    load_pinned_by,
    pinned_by_key,
)


class SpyBackend:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.call_ids: list[str] = []

    async def memory_read(self, key, *, call_id, payload_fn=None):
        self.call_ids.append(call_id)
        return {"key": key, "value": self.store.get(key)}

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.call_ids.append(call_id)
        self.store[key] = value
        return {"key": key, "stored": True}


def run(coro):
    return asyncio.run(coro)


def _index_traffic(backend: SpyBackend) -> list[str]:
    return [c for c in backend.call_ids if c.startswith("memindex-")]


class TestIndexWrite:
    def test_single_pin_writes_one_index_entry(self):
        backend = SpyBackend()
        run(archive_generation(
            backend, task_id="w", eid="e-w1",
            result={}, pins={"a": "e-a1"},
        ))
        doc = backend.store[pinned_by_key("a", "e-a1")]
        assert doc == {
            "consumers": [{"task_id": "w", "eid": "e-w1"}],
            "truncated": False,
        }

    def test_multiple_pins_index_each_upstream(self):
        backend = SpyBackend()
        run(archive_generation(
            backend, task_id="w", eid="e-w1",
            result={}, pins={"a": "e-a1", "b": "e-b1"},
        ))
        assert backend.store[pinned_by_key("a", "e-a1")]["consumers"] == \
            [{"task_id": "w", "eid": "e-w1"}]
        assert backend.store[pinned_by_key("b", "e-b1")]["consumers"] == \
            [{"task_id": "w", "eid": "e-w1"}]

    def test_two_generations_appending_same_upstream(self):
        backend = SpyBackend()
        run(archive_generation(backend, task_id="w1", eid="e-1",
                               result={}, pins={"a": "e-a1"}))
        run(archive_generation(backend, task_id="w2", eid="e-2",
                               result={}, pins={"a": "e-a1"}))
        consumers = backend.store[pinned_by_key("a", "e-a1")]["consumers"]
        assert consumers == [{"task_id": "w1", "eid": "e-1"},
                             {"task_id": "w2", "eid": "e-2"}]

    def test_duplicate_declaration_dedupes(self):
        backend = SpyBackend()
        for _ in range(2):
            run(archive_generation(backend, task_id="w", eid="e-w1",
                                   result={}, pins={"a": "e-a1"}))
        consumers = backend.store[pinned_by_key("a", "e-a1")]["consumers"]
        assert consumers == [{"task_id": "w", "eid": "e-w1"}]

    def test_empty_pins_produce_zero_index_traffic(self):
        backend = SpyBackend()
        run(archive_generation(backend, task_id="w", eid="e-w1",
                               result={}, pins={}))
        assert _index_traffic(backend) == []

    def test_opt_out_produces_zero_index_traffic(self):
        backend = SpyBackend()
        run(archive_generation(backend, task_id="w", eid="e-w1",
                               result={}, pins={"a": "e-a1"},
                               index_pins=False))
        assert pinned_by_key("a", "e-a1") not in backend.store
        assert _index_traffic(backend) == []

    def test_index_call_ids_follow_naming_discipline(self):
        backend = SpyBackend()
        run(archive_generation(backend, task_id="w", eid="e-w1",
                               result={}, pins={"a": "e-a1"}))
        traffic = _index_traffic(backend)
        assert len(traffic) == 2  # one read + one write
        read_cid, write_cid = traffic
        assert read_cid != write_cid  # unique audit event per op
        assert read_cid.startswith("memindex-w-e-w1-91-")
        assert write_cid.startswith("memindex-w-e-w1-90-")
        # The slot hash covers both target coordinates: same writer,
        # different upstream eid -> different slot.
        run(archive_generation(backend, task_id="w", eid="e-w1",
                               result={}, pins={"a": "e-a2"}))
        traffic = _index_traffic(backend)
        assert len({c.rsplit("-", 1)[-1] for c in traffic}) == 2

    def test_consumer_cap_marks_truncation(self):
        backend = SpyBackend()
        key = pinned_by_key("a", "e-a1")
        backend.store[key] = {
            "consumers": [{"task_id": f"w-{i}", "eid": f"e-{i}"}
                          for i in range(_INDEX_CONSUMER_CAP)],
            "truncated": False,
        }
        run(archive_generation(backend, task_id="w-new", eid="e-new",
                               result={}, pins={"a": "e-a1"}))
        doc = backend.store[key]
        assert len(doc["consumers"]) == _INDEX_CONSUMER_CAP
        assert doc["truncated"] is True

    def test_self_loop_pin_indexed(self):
        # The self-loop shape: gen2 pins its own gen1 eid; the index
        # records the same task as its own consumer.
        backend = SpyBackend()
        run(archive_generation(backend, task_id="sup", eid="e-g1",
                               result={}, pins={}))
        assert _index_traffic(backend) == []
        run(archive_generation(backend, task_id="sup", eid="e-g2",
                               result={}, pins={"sup": "e-g1"}))
        consumers = backend.store[pinned_by_key("sup", "e-g1")]["consumers"]
        assert consumers == [{"task_id": "sup", "eid": "e-g2"}]


class TestIndexRead:
    def test_load_returns_consumers(self):
        backend = SpyBackend()
        run(archive_generation(backend, task_id="w", eid="e-w1",
                               result={}, pins={"a": "e-a1"}))
        backend.call_ids.clear()
        doc = run(load_pinned_by(backend, task_id="a", eid="e-a1",
                                 reader_task_id="r", reader_eid="e-r"))
        assert doc == {
            "consumers": [{"task_id": "w", "eid": "e-w1"}],
            "truncated": False,
        }
        # The read attributes to the READER's generation.
        assert any(c.startswith("memindex-r-e-r-91-")
                   for c in backend.call_ids)

    def test_missing_entry_degrades_to_empty_document(self):
        backend = SpyBackend()
        doc = run(load_pinned_by(backend, task_id="ghost", eid="e-g",
                                 reader_task_id="r", reader_eid="e-r"))
        assert doc == {"consumers": [], "truncated": False}

    def test_no_backend_degrades_to_empty_document(self):
        doc = run(load_pinned_by(None, task_id="a", eid="e-a1",
                                 reader_task_id="r", reader_eid="e-r"))
        assert doc == {"consumers": [], "truncated": False}