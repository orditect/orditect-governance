"""Hot-path task-state cleanup between runs.

Migrated from the reference application's runtime cleanup. Task keys
are deleted by explicit name, never by pattern. The deletion list is
built from FACTS: caller-provided static ids plus every task id found
in previous runs' snapshot files (dynamic ids are covered there).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)


def collect_known_task_ids(
    list_snap_files: Callable[[], Iterable[Path]],
    static_ids: Iterable[str],
) -> set[str]:
    """Gather every task id recorded in existing snapshot files."""
    task_ids = set(static_ids)
    for snap in list_snap_files():
        snap = Path(snap)
        if not snap.is_file():
            continue
        for line in snap.read_text().splitlines():
            try:
                tid = json.loads(line).get("data", {}).get("task_id")
            except Exception:
                continue
            if tid:
                task_ids.add(tid)
    return task_ids


class CleanupService:
    """Delete stale hot-path task records by explicit key name."""

    def __init__(self, key_fn=lambda tid: f"task:{tid}") -> None:
        self._key_fn = key_fn

    async def reset_task_state(self, redis_client: Any,
                               task_ids: Iterable[str]) -> None:
        keys = [self._key_fn(tid) for tid in sorted(set(task_ids))]
        if not keys:
            return
        try:
            await redis_client.delete(*keys)
        except Exception as e:
            # Best effort: a cleanup failure must not block a run.
            log.warning("state cleanup best-effort failed (ignored): %s", e)