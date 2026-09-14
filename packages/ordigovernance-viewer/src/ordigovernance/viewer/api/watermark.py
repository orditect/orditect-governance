"""Watermark SSE endpoint: semaphore usage plus budget balance.

Ported from the reference application's water router and generalized.
Hard discipline: usage figures are non-atomic approximations — display
only, never alert on them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def build_watermark_router(
    get_semaphore_status: Callable[[], Any],
    get_budget_balance: Callable[[], Any],
    *,
    prefix: str = "/api/water",
    interval: float = 1.0,
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the watermark SSE router.

    get_semaphore_status() -> awaitable returning the registry status
    (list of dicts or {name: status} mapping; normalized per frame).
    get_budget_balance() -> awaitable returning int | None.
    """
    router = APIRouter(prefix=prefix, tags=tags or ["water"])

    @router.get("/stream")
    async def water_stream():
        from ordigovernance.viewer.semaphore_normalizer import (
            normalize_sems,
        )

        async def event_gen():
            while True:
                try:
                    sems_raw = await get_semaphore_status()
                    cells: dict = {}
                    for s in normalize_sems(sems_raw):
                        name = s.get("name")
                        if not name:
                            continue
                        cells[name] = {
                            "usage": s.get("usage", s.get("in_use", 0)),
                            "limit": s.get("limit", 0),
                            "utilization": s.get("utilization", "?"),
                        }
                    payload = {
                        "semaphores": cells,
                        "budget": await get_budget_balance(),
                    }
                    yield (f"data: {json.dumps(payload, ensure_ascii=False)}"
                           f"\n\n")
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    # Emit an error frame and keep the stream alive.
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                    await asyncio.sleep(interval)

        return StreamingResponse(
            event_gen(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    return router