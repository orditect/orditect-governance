"""Task-level generation archive.

archive key:  gen-result/{task_id}/{eid}

Contains eid by design: generation-level evidence, isolated per
generation. The hot record only holds the LATEST generation; the archive
holds EVERY generation's result + lineage pins, and therefore survives
reopen_task.

Pin reverse index (pinned-by): every archived pin declaration also
maintains an index entry per pinned upstream, mapping the pinned
generation to the generations that declared consumption of it. The
index serves two consumers: the orphan-descendant guard (reopening a
node whose old generation still has ACTIVE consumers is flagged) and
range-drift attribution (consumption links upgrade topological
attributions to pin-declared confidence).

Index trade-offs (disclosed, deliberate):
  - read-modify-write without CAS: two generations pinning the same
    upstream concurrently may lose one update. The index is an
    audit-grade approximation, NOT a transactional structure; callers
    must treat absence of an entry as "unknown", never as "nobody".
  - cost: each archived pin costs one index read plus one index write
    (evidence-chain traffic, INTERNAL class). archive_generation
    accepts index_pins=False for hot paths that opt out.
  - the consumer list is capped; overflow sets truncated=True so
    readers know the list is incomplete.

D5: this module transports the result dict opaquely — it never inspects
business field names. Field interpretation belongs to the bridge/
business layer.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from ordigovernance.api.memo import MemoBackend
from ordigovernance.api.naming import (
    SEQ_ARCHIVE_SAVE,
    archive_load_seq,
    make_call_id,
)

log = logging.getLogger(__name__)


def gen_result_key(task_id: str, eid: str) -> str:
    """Archive key for one generation's result + lineage pins."""
    return f"gen-result/{task_id}/{eid}"


async def archive_generation(backend: MemoBackend | None, *, task_id: str,
                             eid: str, result: dict,
                             pins: dict[str, str],
                             index_pins: bool = True) -> None:
    """Persist one generation's result + lineage pins (survives reopen).

    index_pins: also maintain the pinned-by reverse index (one
    read-modify-write per pin; audit-grade approximation, see the
    module docstring). Pass False on hot paths that opt out of the
    index traffic.
    """
    if backend is None:
        log.warning("archive skipped (no backend): %s@%s", task_id, eid)
        return
    await backend.memory_write(
        gen_result_key(task_id, eid),
        {"result": result, "input_pins": pins},
        call_id=make_call_id("memsave", task_id, eid, seq=SEQ_ARCHIVE_SAVE),
    )
    if index_pins:
        for upstream_task, upstream_eid in pins.items():
            await _index_pin(backend, upstream_task, upstream_eid,
                             consumer_task_id=task_id, consumer_eid=eid)


# ---- pinned-by reverse index -------------------------------------------------

_INDEX_CONSUMER_CAP = 100


def pinned_by_key(task_id: str, eid: str) -> str:
    """Index key for one pinned generation's reverse-consumer list."""
    return f"pinned-by/{task_id}/{eid}"


def _index_slot(upstream_task_id: str, upstream_eid: str) -> str:
    """Content-addressed slot for index call ids (archive-band shape).

    The slot hash covers BOTH target coordinates: every (writer
    generation, index target) pair keeps a unique call id, and the
    read/write pair of one index update carries distinct bands
    (91- = load semantics, 90- = save semantics).
    """
    digest = hashlib.sha256(
        f"{upstream_task_id}@{upstream_eid}".encode("utf-8")
    ).hexdigest()[:6]
    return digest


async def _index_pin(backend: MemoBackend, upstream_task_id: str,
                     upstream_eid: str, *, consumer_task_id: str,
                     consumer_eid: str) -> None:
    """Append one consumer to the pinned-by index of one generation."""
    key = pinned_by_key(upstream_task_id, upstream_eid)
    slot = _index_slot(upstream_task_id, upstream_eid)
    doc = await backend.memory_read(
        key,
        call_id=make_call_id("memindex", consumer_task_id, consumer_eid,
                             seq=f"91-{slot}"),
    )
    value = (doc or {}).get("value") or {}
    consumers = list(value.get("consumers") or [])
    entry = {"task_id": consumer_task_id, "eid": consumer_eid}
    if entry not in consumers:
        consumers.append(entry)
    truncated = bool(value.get("truncated"))
    if len(consumers) > _INDEX_CONSUMER_CAP:
        consumers = consumers[:_INDEX_CONSUMER_CAP]
        truncated = True
    await backend.memory_write(
        key,
        {"consumers": consumers, "truncated": truncated},
        call_id=make_call_id("memindex", consumer_task_id, consumer_eid,
                             seq=f"90-{slot}"),
    )


async def load_pinned_by(backend: MemoBackend | None, *, task_id: str,
                         eid: str, reader_task_id: str,
                         reader_eid: str) -> dict:
    """Read the pinned-by index of one generation.

    Returns {"consumers": [...], "truncated": bool}; a missing entry
    yields an empty, non-truncated document. Absence means "unknown"
    (never indexed, or archived before the index existed), NOT
    "nobody consumes this generation" — callers must treat it as a
    hint, not a proof. The read attributes to the READER's current
    generation.
    """
    if backend is None:
        return {"consumers": [], "truncated": False}
    slot = _index_slot(task_id, eid)
    doc = await backend.memory_read(
        pinned_by_key(task_id, eid),
        call_id=make_call_id("memindex", reader_task_id, reader_eid,
                             seq=f"91-{slot}"),
    )
    value = (doc or {}).get("value") or {}
    return {
        "consumers": list(value.get("consumers") or []),
        "truncated": bool(value.get("truncated")),
    }


async def load_generation(backend: MemoBackend | None, *, task_id: str,
                          eid: str, reader_task_id: str, reader_eid: str,
                          seq: int | str | None = None) -> dict | None:
    """Pinned read of one archived generation.

    The audit event attributes the read to the READER's current
    generation, not to the archived one. Callers performing several
    archive loads within one generation MUST keep a distinct call_id
    per load (unique audit event, unique charge). The default slot is
    archive_load_seq(task_id): content-addressed, stable across
    generations, and deduplicated per target — pass an explicit seq
    only to read the SAME target twice in one generation.

    The audit payload carries the read target explicitly: the call_id
    slot is content-addressed and cannot be reversed into the target
    identity, so pin-vs-consumption reconciliation reads the target
    from the payload.
    """
    if backend is None:
        return None
    doc = await backend.memory_read(
        gen_result_key(task_id, eid),
        call_id=make_call_id(
            "memload", reader_task_id, reader_eid,
            seq=archive_load_seq(task_id) if seq is None else seq,
        ),
        payload_fn=_load_payload_fn(task_id, eid),
    )
    return (doc or {}).get("value")


def _load_payload_fn(task_id: str, eid: str):
    """Audit payload for one archive load: the pinned read target.

    Total by construction: the target is closed over before the read,
    so error/cancel outcomes carry the same fields as a success.
    """

    def payload(_result: Any) -> dict:
        return {"target_task_id": task_id, "target_eid": eid}

    return payload