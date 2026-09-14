# tests/test_naming.py
from ordigovernance.api.naming import (
    SEQ_ARCHIVE_LOAD,
    SEQ_ARCHIVE_SAVE,
    SEQ_MEMO_BASE,
    make_call_id,
)


def test_call_id_without_seq():
    assert make_call_id("plan", "plan", "exec-abc") == "plan-plan-exec-abc"


def test_call_id_with_seq():
    cid = make_call_id("vector", "researcher-a", "exec-0123456789ab", seq=1)
    assert cid == "vector-researcher-a-exec-0123456789ab-1"


def test_seq_namespaces_disjoint_and_ordered():
    assert SEQ_ARCHIVE_SAVE == 90
    assert SEQ_ARCHIVE_LOAD == 91
    assert SEQ_MEMO_BASE == 100
    assert SEQ_MEMO_BASE > SEQ_ARCHIVE_LOAD