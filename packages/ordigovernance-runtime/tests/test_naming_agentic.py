"""Naming discipline: agent band and archive load slots."""

from ordigovernance.api.naming import (
    SEQ_AGENT_BASE,
    SEQ_MEMO_BASE,
    archive_load_seq,
    make_call_id,
)


def test_agent_base_above_memo_band():
    # Memo traffic per generation is capped at 900 by construction.
    assert SEQ_AGENT_BASE >= SEQ_MEMO_BASE + 900


def test_make_call_id_accepts_str_seq():
    cid = make_call_id("memload", "draft", "eid-1", seq="91-abc123")
    assert cid == "memload-draft-eid-1-91-abc123"


def test_archive_load_seq_content_addressed():
    a = archive_load_seq("draft")
    b = archive_load_seq("draft")
    c = archive_load_seq("review")
    assert a == b
    assert a != c
    assert a.startswith("91-")


def test_archive_load_seq_distinct_per_target():
    slots = {archive_load_seq(f"task-{i}") for i in range(50)}
    # Hash suffixes make collisions practically impossible at this
    # scale; the slot space is unbounded unlike the legacy 91-99 band.
    assert len(slots) == 50