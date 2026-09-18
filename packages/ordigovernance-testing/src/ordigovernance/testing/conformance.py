"""Conformance kit: the format-compatibility contract of the
ordigovernance evidence chain.

Any implementation claiming ordigovernance compatibility (an engine tier,
a bridge, a storage adapter) must produce evidence in these exact
shapes. The free tier and the engine tier share these assertions, so
a format drift on either side fails CI on both sides.

Profiles:
  producer    - one governed run's memo/archive/call-id shapes;
  consumer    - read-side tolerances (legacy rows degrade, never crash);
  engine-memo - a memo engine's behavioral contract (result/origin
                shape, origin vocabulary, no fabrication, producer
                discipline, seq totality), driven via
                run_engine_memo_profile.
"""

from __future__ import annotations

import json
from typing import Any

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

# ---- engine-memo profile -----------------------------------------------------


class _EngineProfileBackend:
    """In-memory backend with envelope reads and payload_fn support."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def memory_read(self, key, *, call_id, payload_fn=None):
        result = {"key": key, "value": self.store.get(key)}
        if payload_fn is not None:
            payload_fn(result)
        return result

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.store[key] = value
        ack = {"key": key, "stored": True}
        if payload_fn is not None:
            payload_fn(ack)
        return ack


def _check_engine_result_shape(out: Any, label: str) -> list[str]:
    """(result, origin) shape plus the origin vocabulary."""
    if not (isinstance(out, tuple) and len(out) == 2):
        return [f"{label}: expected a (result, origin) tuple, "
                f"got {type(out).__name__}"]
    result, origin = out
    findings: list[str] = []
    if not isinstance(result, dict):
        findings.append(
            f"{label}: result must be a dict, got {type(result).__name__}")
    if not isinstance(origin, str):
        findings.append(
            f"{label}: origin must be a string, got {type(origin).__name__}")
    elif not (origin in ("executed", "ungoverned")
              or origin.startswith(("reused:", "stubbed:"))):
        findings.append(
            f"{label}: origin {origin!r} is outside the vocabulary "
            f"(executed | ungoverned | reused:<call_id> | stubbed:<purpose>)")
    return findings


async def run_engine_memo_profile(memo_factory) -> list[str]:
    """Drive one memo engine through the engine-memo conformance profile.

    memo_factory matches the runtime injection point:
    factory(backend, *, task_id, eid, previous_status, scope) -> a
    MemoLayerProtocol implementation.

    What the profile asserts (contract, not semantics):
      - every call returns a (result: dict, origin: str) tuple;
      - origin stays inside the documented vocabulary;
      - a "reused:" origin never fabricates content: the result equals
        a result an earlier profile call actually produced for the
        same logical call (purpose, seq, inputs);
      - a producer call (reuse="never") never reports "reused:";
      - int slots and content-addressed string slots are both legal;
      - cancelled-previous + on_resume is a legal input (totality).

    Reuse itself is engine semantics and is NOT required by the
    profile: a passthrough-shaped engine that always executes is
    conformant. Fabrication is not.
    """
    findings: list[str] = []
    backend = _EngineProfileBackend()
    # (purpose, seq, inputs-blob) -> results produced so far.
    produced: dict[tuple, list] = {}

    def _key(purpose, seq, inputs) -> tuple:
        blob = json.dumps(inputs, ensure_ascii=False, sort_keys=True,
                          default=str)
        return (str(purpose), str(seq), blob)

    def _make(eid: str, previous_status: str | None = None):
        return memo_factory(backend, task_id="conf", eid=eid,
                            previous_status=previous_status,
                            scope="conformance")

    async def _drive(label: str, eid: str, purpose: str, seq,
                     inputs: dict, execute, *, reuse: str = "always",
                     previous_status: str | None = None) -> None:
        try:
            out = await _make(eid, previous_status).get_or_execute(
                purpose, seq, inputs, execute, reuse=reuse)
        except Exception as e:
            findings.append(f"{label}: call raised: "
                            f"{type(e).__name__}: {e}")
            return
        findings.extend(_check_engine_result_shape(out, label))
        if not (isinstance(out, tuple) and len(out) == 2
                and isinstance(out[1], str)):
            return
        result, origin = out
        if reuse == "never":
            if origin.startswith("reused:"):
                findings.append(
                    f"{label}: producer call (reuse='never') reported "
                    f"'reused:'; producer call sites refuse every "
                    f"override")
            return
        if origin.startswith("reused:"):
            prior = produced.get(_key(purpose, seq, inputs)) or []
            if not prior:
                findings.append(
                    f"{label}: reported 'reused:' for a logical call "
                    f"with no prior production in the profile backend "
                    f"(fabrication)")
            elif result not in prior:
                findings.append(
                    f"{label}: reported 'reused:' but returned content "
                    f"the earlier generation never produced "
                    f"(fabrication)")
        elif origin == "executed":
            produced.setdefault(_key(purpose, seq, inputs),
                                []).append(result)

    async def _execute_a():
        return {"hits": 3}

    async def _execute_b():
        return {"hits": 99}

    await _drive("gen1", "e1", "search", 1, {"q": "ev"}, _execute_a)
    await _drive("gen2", "e2", "search", 1, {"q": "ev"}, _execute_b)
    await _drive("producer", "e3", "analyze", 1, {"q": "ev"}, _execute_b,
                 reuse="never")
    await _drive("string seq", "e4", "search", "memo-abc123",
                 {"q": "ev"}, _execute_a)
    await _drive("on-resume", "e5", "search", 1, {"q": "ev"}, _execute_a,
                 reuse="on_resume", previous_status="cancelled")
    return findings