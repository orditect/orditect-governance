import asyncio

from ordigovernance.runtime.archive.archive import (
    archive_generation,
    gen_result_key,
    load_generation,
)
from ordigovernance.api.naming import archive_load_seq


def run(coro):
    return asyncio.run(coro)


class FakeBackend:
    def __init__(self):
        self.store = {}
        self.call_ids = []
        self.payloads: list[dict] = []

    async def memory_read(self, key, *, call_id, payload_fn=None):
        self.call_ids.append(call_id)
        result = {"key": key, "value": self.store.get(key)}
        if payload_fn is not None:
            self.payloads.append(payload_fn(result))
        return result

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.call_ids.append(call_id)
        self.store[key] = value
        return {"key": key, "stored": True}


def test_gen_result_key_shape():
    assert gen_result_key("draft", "exec-abc") == "gen-result/draft/exec-abc"


def test_roundtrip_and_call_id_attribution():
    backend = FakeBackend()

    async def main():
        await archive_generation(
            backend, task_id="draft", eid="exec-d1",
            result={"payload": 1}, pins={"upstream": "exec-u1"},
        )
        return await load_generation(
            backend, task_id="draft", eid="exec-d1",
            reader_task_id="draft", reader_eid="exec-d2",
        )

    doc = run(main())
    assert doc["result"] == {"payload": 1}
    assert doc["input_pins"] == {"upstream": "exec-u1"}
    # save attributed to the ARCHIVED generation at seq 90
    assert "memsave-draft-exec-d1-90" in backend.call_ids
    # load attributed to the READER's generation, content-addressed slot
    expected = f"memload-draft-exec-d2-{archive_load_seq('draft')}"
    assert expected in backend.call_ids


def test_multiple_loads_use_distinct_seq():
    backend = FakeBackend()

    async def main():
        await archive_generation(backend, task_id="t", eid="exec-a",
                                 result={}, pins={})
        await load_generation(backend, task_id="t", eid="exec-a",
                              reader_task_id="r", reader_eid="exec-b", seq=91)
        await load_generation(backend, task_id="t", eid="exec-a",
                              reader_task_id="r", reader_eid="exec-b", seq=92)

    run(main())
    assert "memload-r-exec-b-91" in backend.call_ids
    assert "memload-r-exec-b-92" in backend.call_ids


def test_no_backend_degrades_gracefully():
    run(archive_generation(None, task_id="t", eid="e", result={}, pins={}))
    assert run(load_generation(None, task_id="t", eid="e",
                               reader_task_id="r", reader_eid="re")) is None


def test_load_slots_distinct_per_target():
    """Content-addressed slots: stable per target, distinct across targets."""
    backend = FakeBackend()

    async def main():
        await archive_generation(
            backend, task_id="draft", eid="exec-d1",
            result={"x": 1}, pins={},
        )
        await archive_generation(
            backend, task_id="review", eid="exec-r1",
            result={"x": 2}, pins={},
        )
        # Two loads within ONE reader generation must keep unique call ids.
        await load_generation(
            backend, task_id="draft", eid="exec-d1",
            reader_task_id="impl", reader_eid="exec-i1",
        )
        await load_generation(
            backend, task_id="review", eid="exec-r1",
            reader_task_id="impl", reader_eid="exec-i1",
        )

    run(main())
    loads = [c for c in backend.call_ids if c.startswith("memload-impl-")]
    assert len(loads) == 2
    assert len(set(loads)) == 2  # unique audit event per load


def test_load_generation_memload_payload_carries_target():
    backend = FakeBackend()

    async def main():
        await archive_generation(backend, task_id="draft", eid="exec-d1",
                                 result={"x": 1}, pins={})
        await load_generation(backend, task_id="draft", eid="exec-d1",
                              reader_task_id="impl", reader_eid="exec-i1")

    run(main())
    assert {"target_task_id": "draft",
            "target_eid": "exec-d1"} in backend.payloads


def test_multiple_loads_carry_distinct_targets():
    backend = FakeBackend()

    async def main():
        await archive_generation(backend, task_id="a", eid="e-a",
                                 result={}, pins={})
        await archive_generation(backend, task_id="b", eid="e-b",
                                 result={}, pins={})
        await load_generation(backend, task_id="a", eid="e-a",
                              reader_task_id="r", reader_eid="e-r")
        await load_generation(backend, task_id="b", eid="e-b",
                              reader_task_id="r", reader_eid="e-r")

    run(main())
    targets = [p for p in backend.payloads if "target_task_id" in p]
    assert targets == [
        {"target_task_id": "a", "target_eid": "e-a"},
        {"target_task_id": "b", "target_eid": "e-b"},
    ]