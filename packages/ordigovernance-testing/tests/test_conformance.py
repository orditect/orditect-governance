"""Conformance kit self-tests: the check_* assertions and the producer
profile against hand-built shapes and one synthetic bundle."""

from __future__ import annotations

from ordigovernance.testing.conformance import (
    check_archive_document,
    check_call_id_shape,
    check_memo_envelope,
    check_pinned_by_document,
    check_seq_bands,
    run_engine_memo_profile,
    run_producer_profile,
)


class TestCallIdShape:
    def test_valid_call_id_passes(self):
        errors = check_call_id_shape(
            "search-researcher-a-exec-1-3", "search",
            "researcher-a", "exec-1")
        assert errors == []

    def test_call_id_without_seq_passes(self):
        errors = check_call_id_shape(
            "plan-plan-exec-abc", "plan", "plan", "exec-abc")
        assert errors == []

    def test_wrong_head_flagged(self):
        errors = check_call_id_shape(
            "vector-plan-exec-abc", "search", "plan", "exec-abc")
        assert errors

    def test_malformed_suffix_flagged(self):
        errors = check_call_id_shape(
            "plan-plan-exec-abcX1", "plan", "plan", "exec-abc")
        assert errors


class TestSeqBands:
    def test_bands_contract_holds(self):
        assert check_seq_bands() == []


class TestMemoEnvelope:
    def test_full_envelope_passes(self):
        doc = {"result": {}, "origin_call_id": "c", "origin_eid": "e"}
        assert check_memo_envelope(doc) == []

    def test_missing_fields_flagged(self):
        errors = check_memo_envelope({"result": {}})
        assert len(errors) == 2


class TestArchiveDocument:
    def test_full_document_passes(self):
        doc = {"result": {}, "input_pins": {"a": "e-a1"}}
        assert check_archive_document(doc) == []

    def test_missing_pins_flagged(self):
        assert check_archive_document({"result": {}})

    def test_non_dict_pins_flagged(self):
        assert check_archive_document(
            {"result": {}, "input_pins": ["a"]})


class TestPinnedByDocument:
    def test_full_document_passes(self):
        doc = {"consumers": [{"task_id": "w", "eid": "e-w1"}],
               "truncated": False}
        assert check_pinned_by_document(doc) == []

    def test_missing_consumers_flagged(self):
        assert check_pinned_by_document({"truncated": False})

    def test_malformed_consumer_flagged(self):
        errors = check_pinned_by_document(
            {"consumers": [{"task_id": "w"}], "truncated": False})
        assert errors


class TestProducerProfile:
    def test_clean_bundle_passes(self):
        bundle = {
            "audit.ndjson": [
                {"data": {"event_id": "memget-w-e1-101",
                          "event_type": "memory_call",
                          "payload": {"decision": "hit"}}},
                {"data": {"event_id": "analyze-w-e1-3",
                          "event_type": "llm_call",
                          "payload": {}}},
            ],
            "snapshots.ndjson": [],
            "deps.ndjson": [],
        }
        assert run_producer_profile(bundle) == []

    def test_memory_row_without_payload_flagged(self):
        bundle = {
            "audit.ndjson": [
                {"data": {"event_id": "memput-w-e1-101",
                          "event_type": "memory_call"}},
            ],
        }
        errors = run_producer_profile(bundle)
        assert errors and any("payload" in e for e in errors)

def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _PassthroughEngine:
    """Always-execute engine: conformant by construction."""

    def __init__(self, backend, **kwargs):
        self._backend = backend

    @classmethod
    def factory(cls, backend, **kwargs):
        return cls(backend, **kwargs)

    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        return await execute(), "executed"


class _CachingEngine:
    """Minimal correct caching engine (the profile's fixed inputs make
    purpose/seq a sufficient key here)."""

    def __init__(self, backend, **kwargs):
        self._backend = backend

    @classmethod
    def factory(cls, backend, **kwargs):
        return cls(backend, **kwargs)

    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        if reuse == "never":
            return await execute(), "executed"
        key = f"{purpose}/{seq}"
        doc = await self._backend.memory_read(key, call_id="conf")
        if doc and doc.get("value") is not None:
            return doc["value"]["result"], "reused:conf"
        result = await execute()
        await self._backend.memory_write(
            key, {"result": result, "origin_eid": "conf"}, call_id="conf")
        return result, "executed"


class _FabricatingEngine:
    """Reports reused on every call while returning fresh content."""

    def __init__(self, backend, **kwargs):
        self._n = 0

    @classmethod
    def factory(cls, backend, **kwargs):
        return cls(backend, **kwargs)

    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        self._n += 1
        return {"n": self._n}, "reused:nowhere"


class _ProducerReusingEngine:
    """Reuses even for producer call sites."""

    @classmethod
    def factory(cls, backend, **kwargs):
        return cls()

    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        return {"v": 1}, "reused:conf"


class _IntOnlyEngine:
    """Rejects content-addressed string seq slots."""

    @classmethod
    def factory(cls, backend, **kwargs):
        return cls()

    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        if not isinstance(seq, int):
            raise TypeError("int seq only")
        return await execute(), "executed"


class _ShapelessEngine:
    """Returns a bare dict instead of the (result, origin) tuple."""

    @classmethod
    def factory(cls, backend, **kwargs):
        return cls()

    async def get_or_execute(self, purpose, seq, inputs, execute, *,
                             reuse="always", origin_seq=None, mode=None):
        return {"result": 1}


class TestEngineMemoProfile:
    def test_passthrough_engine_is_conformant(self):
        assert _run(run_engine_memo_profile(_PassthroughEngine.factory)) == []

    def test_caching_engine_is_conformant(self):
        assert _run(run_engine_memo_profile(_CachingEngine.factory)) == []

    def test_fabricating_engine_flagged(self):
        findings = _run(run_engine_memo_profile(_FabricatingEngine.factory))
        assert findings
        assert any("fabrication" in f for f in findings)

    def test_producer_reuse_flagged(self):
        findings = _run(run_engine_memo_profile(
            _ProducerReusingEngine.factory))
        assert any("producer" in f for f in findings)

    def test_string_seq_rejection_flagged(self):
        findings = _run(run_engine_memo_profile(_IntOnlyEngine.factory))
        assert any("string seq" in f for f in findings)

    def test_shape_violation_flagged(self):
        findings = _run(run_engine_memo_profile(_ShapelessEngine.factory))
        assert findings
        assert any("tuple" in f for f in findings)