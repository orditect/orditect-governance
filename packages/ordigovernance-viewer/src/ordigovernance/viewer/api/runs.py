"""Run lifecycle endpoints, parameterized by an injected run manager.

Start/list/inspect runs and subscribe to the active run's SSE event
channel. All run vocabulary comes from the manager/registry pair; this
module carries none.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


class StartRunRequest(BaseModel):
    intent: str = Field(min_length=1)
    params: dict = Field(default_factory=dict)


def build_runs_router(
    manager: Any,
    *,
    prefix: str = "/api/runs",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the run-lifecycle router over a SingleRunManager."""
    router = APIRouter(prefix=prefix, tags=tags or ["runs"])
    registry = manager.registry

    @router.post("")
    async def start_run(req: StartRunRequest):
        """Start one background run; 409 when another run is active."""
        try:
            run_id = await manager.start_run(req.intent, req.params)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if run_id is None:
            raise HTTPException(status_code=409,
                                detail="a run is already in progress")
        return {"run_id": run_id, "started": True}

    @router.get("")
    async def list_runs():
        """Runs registry, newest first — the version selector's source."""
        return registry.list_runs()

    @router.get("/active/status")
    async def active_status():
        """Current run state: idle/running plus the active run id."""
        return manager.state

    @router.get("/{run_id}")
    async def get_run(run_id: str):
        entry = registry.get_run(run_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown run_id")
        return entry

    @router.get("/{run_id}/events")
    async def run_events(run_id: str):
        """SSE channel for run events.

        For the ACTIVE run this is a live broadcast (plus keepalives).
        For a historical run the channel emits that run's registry
        entry once and closes.
        """
        if manager.active_run_id != run_id:
            entry = registry.get_run(run_id)
            if entry is None:
                raise HTTPException(status_code=404,
                                    detail="unknown run_id")

            async def historical():
                yield ("data: " + json.dumps(
                    {"type": "run_state", "status": "finished",
                     "run_id": run_id, "entry": entry},
                    ensure_ascii=False) + "\n\n")

            return StreamingResponse(
                historical(), media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )

        async def live():
            q = manager.bus.subscribe()
            try:
                yield ("data: " + json.dumps(
                    {"type": "run_state", **manager.state},
                    ensure_ascii=False) + "\n\n")
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield ("data: "
                               + json.dumps(event, ensure_ascii=False)
                               + "\n\n")
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                manager.bus.unsubscribe(q)

        return StreamingResponse(
            live(), media_type="text/event-stream", headers=_SSE_HEADERS,
        )

    return router