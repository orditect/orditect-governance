"""Conformance kit: the format-compatibility contract of the
ordigovernance evidence chain.

Any implementation claiming ordigovernance compatibility (an engine tier,
a bridge, a storage adapter) must produce evidence in these exact
shapes. The free tier and the engine tier share these assertions, so
a format drift on either side fails CI on both sides.

Profiles:
  producer - one governed run's memo/archive/call-id shapes;
  consumer - read-side tolerances (legacy rows degrade, never crash).
"""

from __future__ import annotations

from ordigovernance.api.naming import (
    SEQ_AGENT_BASE,
    SEQ_ARCHIVE_SAVE,
    SEQ_MEMO_BASE,
    archive_load_seq,
    make_call_id,
)


def check_call_id_shape(call_id: str, purpose: str, task_id: str,
                        eid: str) -> list[str]:
    """Assert the {purpose}-{task_id}-{eid}[-seq] wire format."""
    errors: list[str] = []
    head = f"{purpose}-{task_id}-{eid}"
    if not call_id.startswith(head):
        errors.append(f"call_id {call_id!r} does not start with {head!r}")
    tail = call_id[len(head):]
    if tail and not tail.startswith("-"):
        errors.append(f"call_id {call_id!r} has a malformed seq suffix")
    return errors


def check_seq_bands() -> list[str]:
    """The naming-discipline band contract (90/91/100/1000)."""
    errors: list[str] = []
    if not (SEQ_ARCHIVE_SAVE == 90):
        errors.append("SEQ_ARCHIVE_SAVE must be 90")
    if not (SEQ_MEMO_BASE > 91):
        errors.append("memo band must sit above the archive band")
    if not (SEQ_AGENT_BASE >= SEQ_MEMO_BASE + 900):
        errors.append("agent band must sit above the memo band cap")
    slot = archive_load_seq("any-target")
    if not slot.startswith("91-"):
        errors.append(f"archive_load_seq slot malformed: {slot!r}")
    return errors


def check_memo_envelope(value: dict) -> list[str]:
    """The memo-entry envelope shape engines and readers both rely on."""
    errors: list[str] = []
    for field_name in ("result", "origin_call_id", "origin_eid"):
        if field_name not in value:
            errors.append(f"memo envelope missing {field_name!r}")
    return errors


def check_archive_document(doc: dict) -> list[str]:
    """The gen-result archive document shape."""
    errors: list[str] = []
    if "result" not in doc:
        errors.append("archive document missing 'result'")
    if "input_pins" not in doc:
        errors.append("archive document missing 'input_pins'")
    pins = doc.get("input_pins")
    if pins is not None and not isinstance(pins, dict):
        errors.append("input_pins must be a dict[str, str]")
    return errors


def check_pinned_by_document(doc: dict) -> list[str]:
    """The pinned-by reverse-index document shape."""
    errors: list[str] = []
    consumers = doc.get("consumers")
    if not isinstance(consumers, list):
        errors.append("pinned-by document must carry a consumers list")
    else:
        for entry in consumers:
            if not {"task_id", "eid"} <= set(entry):
                errors.append(f"consumer entry malformed: {entry!r}")
    if "truncated" not in doc:
        errors.append("pinned-by document missing 'truncated'")
    return errors


def run_producer_profile(bundle: dict) -> list[str]:
    """Run the producer profile against one normalized trace bundle
    (testing.golden.normalize_bundle output shape)."""
    errors: list[str] = []
    errors.extend(check_seq_bands())
    for row in bundle.get("audit.ndjson", []):
        data = row.get("data", row)
        event_id = str(data.get("event_id", ""))
        if event_id.startswith("memget-") or event_id.startswith("memput-"):
            if not isinstance(data.get("payload"), dict):
                errors.append(f"{event_id}: memory rows must carry a payload")
    return errors