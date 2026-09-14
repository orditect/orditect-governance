"""Generation content endpoint (D5: result dict transported opaquely).

Reads ONE archived generation (gen-result/{task_id}/{eid}) through the
MemoBackend surface and returns it sanitized to JSON-safe primitives.
The component layer never interprets business fields of the result.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from ordigovernance.runtime.archive.archive import gen_result_key
from ordigovernance.runtime.lifecycle.event_bus import jsonable
from ordigovernance.api.memo import MemoBackend
from ordigovernance.api.naming import archive_load_seq, make_call_id


def _sanitize(value: Any) -> Any:
    """Keep primitives/containers; degrade unknown objects via jsonable."""
    if isinstance(value, (str, int, float, bool, list, dict, type(None))):
        return jsonable(value)
    return str(value)


def build_generation_router(
    get_backend: Callable[[str], MemoBackend],
    *,
    prefix: str = "/api/runs/{run_id}",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the generation-content router.

    get_backend(run_id) -> MemoBackend for that run's archive store.
    The read is attributed to a fixed viewer identity (no governed
    charge semantics on the cold read path).
    """
    router = APIRouter(prefix=prefix, tags=tags or ["trace"])

    @router.get("/generations/{task_id}/{eid}/content")
    async def get_generation_content(run_id: str, task_id: str, eid: str):
        backend = get_backend(run_id)
        doc = await backend.memory_read(
            gen_result_key(task_id, eid),
            # Cold-path viewer read attributed to a fixed viewer
            # identity; the slot keeps one unique call_id per target.
            call_id=make_call_id(
                "memload", f"viewer-{task_id}", eid,
                seq=archive_load_seq(task_id),
            ),
        )
        value = (doc or {}).get("value")
        if value is None:
            raise HTTPException(
                status_code=404,
                detail=f"no archive for {task_id}@{eid}",
            )
        result = value.get("result", {})
        return {
            "task_id": task_id,
            "execution_id": eid,
            "result": {k: _sanitize(v) for k, v in result.items()},
            "input_pins": jsonable(value.get("input_pins", {})),
        }

    return router