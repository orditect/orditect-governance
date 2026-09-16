"""Composite endpoints: drive-level background drivers (D10).

A composite is a run-scoped background driver coroutine registered in
the deployment registry (e.g. a quality-gate pair). It submits its
child tasks under the run root and evidence closes through the child
tasks themselves. The supervisor-node packaging is deferred to V2 by
design (docs/pitfalls.md 4/13.1 constraints).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ordigovernance.gateway.schemas import (
    CompositeAcceptedResponse,
    CompositeRequest,
    CompositeStatusResponse,
)
from ordigovernance.gateway.session import UnknownVocabularyError

log = logging.getLogger(__name__)


def build_composites_router(
    get_manager,
    *,
    auth_dependency: Any = None,
    prefix: str = "/runs",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the composites router over a SessionManager accessor."""
    router = APIRouter(prefix=prefix, tags=tags or ["composites"],
                       dependencies=([Depends(auth_dependency)]
                                     if auth_dependency else []))

    def _active_session(run_id: str):
        manager = get_manager()
        if manager.active is not None and manager.active.run_id == run_id:
            return manager.active
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not active; composites are "
                   f"valid only while the run lives")

    @router.post("/{run_id}/composites", status_code=202)
    async def start_composite(
            run_id: str, req: CompositeRequest) -> CompositeAcceptedResponse:
        """Start one registered composite as a background driver."""
        session = _active_session(run_id)
        try:
            composite_id = session.start_composite(req.name, req.params)
        except UnknownVocabularyError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        return CompositeAcceptedResponse(composite_id=composite_id,
                                         accepted=True)

    @router.get("/{run_id}/composites/{composite_id}")
    async def get_composite(
            run_id: str, composite_id: str) -> CompositeStatusResponse:
        """Poll one composite's status, children and outcome."""
        session = _active_session(run_id)
        try:
            status = await session.composite_status(composite_id)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown composite {composite_id!r} in run "
                       f"{run_id!r}") from None
        return CompositeStatusResponse(**status)

    return router