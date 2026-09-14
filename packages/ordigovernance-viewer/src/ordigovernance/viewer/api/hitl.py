"""HITL endpoints: pause / resume / retry against the ACTIVE run.

Actions are queue-shaped and execute asynchronously via the run's
dispatcher; each endpoint returns the acceptance receipt and clients
poll /receipt/{action_id} for the execution receipt.

Retry discipline (direct reopen semantics): only the target node is
reopened, never its ancestors — scope reopen would require the whole
ancestor chain to be terminal, which mid-run ancestors are not. Nodes
whose retry would invalidate structural decisions (a planner whose new
output changes the fan-out set) should be listed in non_retryable:
that is a new run, not a retry. The vocabulary of WHICH ids are
non-retryable is injected by the caller.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from fastapi import APIRouter, HTTPException

_TERMINAL_WORDS = frozenset({"succeeded", "failed", "cancelled"})


def build_hitl_router(
    get_resources: Callable[[], Any],
    *,
    non_retryable: Iterable[str] = (),
    task_factory: Callable[[str], Any] | None = None,
    deps_reader: Any = None,
    prefix: str = "/api/hitl",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the HITL router.

    get_resources() -> the active run's resources (must expose .sink,
    .orchestrator and, for retry, the hot storage via .task_io or a
    storage attr); None or missing sink -> 409 no active run.
    task_factory: rebuild one task instance by id for direct retry;
    defaults to resources.task_factory.
    deps_reader: optional dependency-graph read surface. When wired,
    a direct retry of a node that still has ACTIVE descendants
    (non-terminal children along the declared edges) is rejected with
    409 instead of silently minting a new parent generation while the
    old-generation children keep running (orphan-descendant guard).
    """
    router = APIRouter(prefix=prefix, tags=tags or ["hitl"])
    blocked = frozenset(non_retryable)

    def _resources():
        resources = get_resources()
        if resources is None or getattr(resources, "sink", None) is None:
            raise HTTPException(status_code=409, detail="no active run")
        return resources

    @router.post("/pause/{node_id}")
    async def hitl_pause(node_id: str):
        """Enqueue a pause action (node settles as cancelled)."""
        resources = _resources()
        receipt = await resources.sink.pause_node(node_id, actor="hitl-ui")
        return {"action_id": receipt.action_id,
                "accepted": receipt.accepted}

    @router.post("/resume/{root_id}")
    async def hitl_resume(root_id: str):
        """Enqueue a resume-tree action: reuse succeeded, rerun the rest."""
        resources = _resources()
        receipt = await resources.sink.resume_tree(root_id, actor="hitl-ui")
        return {"action_id": receipt.action_id,
                "accepted": receipt.accepted}

    @router.post("/retry/{node_id}")
    async def hitl_retry(node_id: str):
        """Direct reopen of one terminal node on a new generation."""
        if node_id in blocked:
            raise HTTPException(
                status_code=422,
                detail=f"{node_id} is not retryable standalone; "
                       f"start a new run instead",
            )
        resources = _resources()
        storage = getattr(resources, "task_io", None) or getattr(
            resources, "storage", None)
        if storage is None:
            raise HTTPException(status_code=409,
                                detail="hot storage not available")
        rec = await storage.get_task(node_id)
        if not rec or rec.get("status") not in _TERMINAL_WORDS:
            raise HTTPException(
                status_code=409,
                detail=f"node {node_id} is not terminal "
                       f"(current: "
                       f"{rec.get('status') if rec else 'missing'})",
            )
        factory = task_factory or getattr(resources, "task_factory", None)
        if factory is None:
            raise HTTPException(status_code=409,
                                detail="task factory not available")
        if deps_reader is not None:
            from ordigovernance.runtime.orchestration.orphan_guard import (
                ActiveDescendantsError,
                assert_no_active_descendants,
            )

            try:
                await assert_no_active_descendants(
                    node_id, deps_reader=deps_reader, task_io=storage,
                )
            except ActiveDescendantsError as e:
                raise HTTPException(status_code=409, detail=str(e))
        await storage.reopen_task(node_id)
        task = await factory(node_id)
        await resources.orchestrator.submit(task, task_id=node_id)
        return {"action_id": f"retry-direct-{node_id}", "accepted": True}

    @router.get("/receipt/{action_id}")
    async def hitl_receipt(action_id: str):
        """Fetch one action's execution receipt (404 while pending)."""
        resources = get_resources()
        if resources is None or getattr(resources, "sink", None) is None:
            raise HTTPException(
                status_code=404,
                detail="receipt not available (run ended)",
            )
        receipt = await resources.sink.get_receipt(action_id)
        if receipt is None:
            raise HTTPException(
                status_code=404,
                detail="receipt not available (action pending or run ended)",
            )
        getter = (receipt.get if isinstance(receipt, dict)
                  else lambda k, d=None: getattr(receipt, k, d))
        return {
            "action_id": getter("action_id"),
            "action_type": getter("action_type"),
            "status": getter("status"),
            "detail": getter("detail"),
        }

    return router