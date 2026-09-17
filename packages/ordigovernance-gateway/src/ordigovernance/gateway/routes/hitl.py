"""HITL plane endpoints: pause / resume / retry against the ACTIVE run.

Mirrors the viewer's HITL semantics (viewer/api/hitl.py), run-scoped:
actions are queue-shaped and execute asynchronously via the session's
dispatcher; each mutating endpoint returns the acceptance receipt and
clients poll the receipt endpoint for the execution receipt (dual
receipt discipline, docs/pitfalls.md 13.7). Every endpoint 404s once
the run is no longer active: HITL is valid only while the run lives
(docs/pitfalls.md 13.6).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ordigovernance.gateway.schemas import (
    ActionAcceptedResponse,
    HitlActionRequest,
)

log = logging.getLogger(__name__)

TERMINAL_WORDS = frozenset({"succeeded", "failed", "cancelled"})


def build_hitl_router(
    get_manager,
    *,
    auth_dependency: Any = None,
    prefix: str = "/runs",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the HITL router over a SessionManager accessor."""
    router = APIRouter(prefix=prefix, tags=tags or ["hitl"],
                       dependencies=([Depends(auth_dependency)]
                                     if auth_dependency else []))

    def _active_session(run_id: str):
        manager = get_manager()
        if manager.active is not None and manager.active.run_id == run_id:
            return manager.active
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not active; HITL is valid only "
                   f"while the run lives (docs/pitfalls.md 13.6)")

    @router.post("/{run_id}/hitl/pause")
    async def hitl_pause(run_id: str,
                         req: HitlActionRequest) -> ActionAcceptedResponse:
        """Enqueue a pause action (the node settles as cancelled)."""
        session = _active_session(run_id)
        if not req.task_id:
            raise HTTPException(status_code=422,
                                detail="task_id is required")
        receipt = await session.resources.sink.pause_node(
            req.task_id, actor="hitl-gateway")
        return ActionAcceptedResponse(action_id=receipt.action_id,
                                      accepted=receipt.accepted)

    @router.post("/{run_id}/hitl/resume")
    async def hitl_resume(run_id: str,
                          req: HitlActionRequest) -> ActionAcceptedResponse:
        """Enqueue a resume-tree action: reuse succeeded, rerun the rest."""
        session = _active_session(run_id)
        root_id = req.root_id or session.root_id
        receipt = await session.resources.sink.resume_tree(
            root_id, actor="hitl-gateway")
        return ActionAcceptedResponse(action_id=receipt.action_id,
                                      accepted=receipt.accepted)

    @router.post("/{run_id}/hitl/retry")
    async def hitl_retry(run_id: str,
                         req: HitlActionRequest) -> ActionAcceptedResponse:
        """Direct reopen of one terminal node on a new generation.

        Only the target node is reopened, never its ancestors. The
        orphan-descendant guard rejects a retry whose declared children
        are still ACTIVE. The task is rebuilt from the session's
        registered descriptor (the single construction source,
        docs/pitfalls.md 13.8).
        """
        session = _active_session(run_id)
        task_id = req.task_id
        if not task_id:
            raise HTTPException(status_code=422,
                                detail="task_id is required")
        if task_id == session.root_id:
            raise HTTPException(
                status_code=422,
                detail="the run root is not retryable standalone; "
                       "start a new run instead")
        storage = session.hot["storage"]
        record = await storage.get_task(task_id)
        if not record or record.get("status") not in TERMINAL_WORDS:
            raise HTTPException(
                status_code=409,
                detail=f"node {task_id} is not terminal "
                       f"(current: "
                       f"{record.get('status') if record else 'missing'})")
        if task_id not in session.descriptors:
            raise HTTPException(
                status_code=404,
                detail=f"no descriptor registered for {task_id!r} in "
                       f"run {run_id!r}; only gateway-submitted tasks "
                       f"can be retried")
        deps_reader = getattr(session.resources.store, "dependency", None)
        if deps_reader is not None:
            from ordigovernance.runtime.orchestration.orphan_guard import (
                ActiveDescendantsError,
                assert_no_active_descendants,
            )

            try:
                await assert_no_active_descendants(
                    task_id, deps_reader=deps_reader, task_io=storage)
            except ActiveDescendantsError as e:
                raise HTTPException(status_code=409, detail=str(e))
        await storage.reopen_task(task_id)
        task = await session.task_factory(task_id)
        await session.resources.orchestrator.submit(task, task_id=task_id)
        action_id = f"retry-direct-{task_id}"
        # Direct (non-sink) actions still owe the client a receipt
        # (pitfalls 16.8): the reopen + resubmit above is synchronous,
        # so the execution receipt is available immediately instead of
        # 404ing forever behind the sink's receipt table.
        session.direct_receipts[action_id] = {
            "action_id": action_id,
            "action_type": "retry",
            "status": "executed",
            "detail": f"reopened and resubmitted {task_id!r}",
        }
        return ActionAcceptedResponse(action_id=action_id,
                                      accepted=True)

    @router.get("/{run_id}/hitl/receipt/{action_id}")
    async def hitl_receipt(run_id: str, action_id: str) -> dict:
        """Fetch one action's execution receipt (404 while pending)."""
        session = _active_session(run_id)
        receipt = await session.resources.sink.get_receipt(action_id)
        if receipt is None:
            direct = session.direct_receipts.get(action_id)
            if direct is not None:
                return direct
            raise HTTPException(
                status_code=404,
                detail="receipt not available (action pending)")
        getter = (receipt.get if isinstance(receipt, dict)
                  else lambda k, d=None: getattr(receipt, k, d))
        return {
            "action_id": getter("action_id"),
            "action_type": getter("action_type"),
            "status": getter("status"),
            "detail": getter("detail"),
        }

    return router