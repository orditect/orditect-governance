"""Naming discipline: call_id shape and seq slot namespaces.

Migrated from the reference application's app/workflow/ids.py — only the
governance-relevant parts. Business ids (task-id constants, slug rules)
stay in the business layer.

Contracts enforced here:

1. call_id shape (dual-habitat idempotency):
       {purpose}-{task_id}-{execution_id}[-{seq}]
   A really-executed call always lands under the CURRENT generation's
   call_id. Old call_ids appear only as memo origin references and are
   NEVER recycled.

2. seq slot namespaces:
       0-9        business call sites (hand-assigned, stable per site)
       90         generation archive save
       91[-hash]  generation archive loads (see archive_load_seq)
       100-999    atomic memo get/put traffic (monotonic per generation)
       1000+      agent-internal loop calls (tracked atoms, monotonic
                  per generation; see agentic/tracked_*)

   The memo band implicitly caps memo traffic at 900 calls per
   generation; agent-loop traffic lives above 1000 and never collides
   with the memo band.

3. archive load slots:
   Repeated archive loads inside one generation need a distinct call_id
   each (unique audit event, unique charge). Sequential slot assignment
   (91, 92, ...) couples the id to the execution path; archive_load_seq
   derives the slot from the TARGET id instead — content-addressed,
   stable across generations and runs, deduplicated per target, and
   unbounded.
"""

from __future__ import annotations

import hashlib

SEQ_ARCHIVE_SAVE = 90
SEQ_ARCHIVE_LOAD = 91
SEQ_MEMO_BASE = 100
SEQ_AGENT_BASE = 1000


def make_call_id(purpose: str, task_id: str, execution_id: str,
                 seq: int | str | None = None) -> str:
    """Compose the idempotency/audit identity of one governed call."""
    base = f"{purpose}-{task_id}-{execution_id}"
    return f"{base}-{seq}" if seq is not None else base


def archive_load_seq(target_task_id: str) -> str:
    """Stable archive-load slot for one target within any generation.

    Returns "91-<hash(target)>" so every (reader generation, target)
    pair keeps a unique call_id without path-dependent counters.
    """
    digest = hashlib.sha256(target_task_id.encode("utf-8")).hexdigest()[:6]
    return f"91-{digest}"