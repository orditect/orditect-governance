"""Conformance kit self-tests: the check_* assertions and the producer
profile against hand-built shapes and one synthetic bundle."""

from __future__ import annotations

from ordigovernance.testing.conformance import (
    check_archive_document,
    check_call_id_shape,
    check_memo_envelope,
    check_pinned_by_document,
    check_seq_bands,
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