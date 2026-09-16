"""Task-plane endpoints: run lifecycle and descriptor submission.

Vocabulary discipline: the registry is process-global, not
run-scoped, so the vocabulary endpoint also answers on the ambient
run -- n8n nodes query it at DESIGN time, when no user run exists
yet. Task reads stay active-run-only (D14): historical evidence
belongs to the viewer cold path.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ordigovernance.gateway.schemas import (
    FinishRunResponse,
    StartRunRequest,
    StartRunResponse,
    TaskAcceptedResponse,
    TaskDescriptor,
    TaskRecordResponse,
    VocabularyEntry,
    VocabularyResponse,
)
from ordigovernance.gateway.session import (
    AMBIENT_RUN_ID,
    DuplicateTaskError,
    UnknownVocabularyError,
)

log = logging.getLogger(__name__)

TERMINAL_WORDS = frozenset({"succeeded", "failed", "cancelled"})


def build_runs_router(
    get_manager,
    *,
    auth_dependency: Any = None,
    prefix: str = "/runs",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the task-plane router over a SessionManager accessor."""
    router = APIRouter(prefix=prefix, tags=tags or ["runs"],
                       dependencies=([Depends(auth_dependency)]
                                     if auth_dependency else []))

    def _active_session(run_id: str):
        manager = get_manager()
        if manager.active is not None and manager.active.run_id == run_id:
            return manager.active
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not active; historical evidence "
                   f"reads belong to the viewer cold path (D14)")

    def _session_or_ambient(run_id: str):
        manager = get_manager()
        if run_id == AMBIENT_RUN_ID and manager.ambient is not None:
            return manager.ambient
        return _active_session(run_id)

    @router.post("", status_code=201)
    async def start_run(req: StartRunRequest) -> StartRunResponse:
        session = await get_manager().start_user_run(
            req.run_id, budget_max_units=req.budget_max_units)
        if session is None:
            active = get_manager().active
            raise HTTPException(
                status_code=409,
                detail=f"a run is already in progress: "
                       f"{active.run_id!r}")
        return StartRunResponse(run_id=session.run_id, status="running")

    @router.get("")
    async def list_runs() -> list[dict]:
        return get_manager().runs.list_runs()

    @router.get("/{run_id}")
    async def get_run(run_id: str) -> dict:
        manager = get_manager()
        entry = manager.runs.get_run(run_id)
        if entry is None:
            raise HTTPException(status_code=404,
                                detail=f"unknown run {run_id!r}")
        if manager.active is not None and manager.active.run_id == run_id:
            tasks = []
            for tid in manager.active.descriptors:
                record = await manager.active.hot["storage"].get_task(tid)
                tasks.append({
                    "task_id": tid,
                    "status": record.get("status"),
                    "execution_id": record.get("execution_id"),
                })
            entry = {**entry, "tasks": tasks}
        return entry

    @router.post("/{run_id}/tasks", status_code=201)
    async def submit_task(run_id: str,
                          descriptor: TaskDescriptor
                          ) -> TaskAcceptedResponse:
        session = _active_session(run_id)
        try:
            await session.submit_task(descriptor)
        except UnknownVocabularyError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        except DuplicateTaskError as e:
            raise HTTPException(
                status_code=409,
                detail=f"task {e.args[0]!r} is already submitted in "
                       f"run {run_id!r}; a rerun goes through HITL "
                       f"retry") from None
        return TaskAcceptedResponse(task_id=descriptor.task_id,
                                    accepted=True)

    @router.get("/{run_id}/tasks/{task_id}")
    async def get_task(run_id: str,
                       task_id: str) -> TaskRecordResponse:
        session = _active_session(run_id)
        record = await session.hot["storage"].get_task(task_id)
        if not record:
            raise HTTPException(
                status_code=404,
                detail=f"unknown task {task_id!r} in run {run_id!r}")
        return TaskRecordResponse(
            task_id=task_id,
            status=record.get("status"),
            execution_id=record.get("execution_id"),
            previous_execution_ids=list(
                record.get("previous_execution_ids") or []),
            result=record.get("result"))

    @router.post("/{run_id}/finish")
    async def finish_run(run_id: str) -> FinishRunResponse:
        manager = get_manager()
        session = _active_session(run_id)
        statuses: dict[str, str | None] = {}
        for tid in session.descriptors:
            record = await session.hot["storage"].get_task(tid)
            statuses[tid] = record.get("status")
        non_terminal = [
            {"task_id": tid, "status": status}
            for tid, status in statuses.items()
            if status not in TERMINAL_WORDS
        ]
        if non_terminal:
            raise HTTPException(
                status_code=409,
                detail={"non_terminal_tasks": non_terminal})
        final_status = ("succeeded"
                        if all(s == "succeeded" for s in statuses.values())
                        else "finished_with_errors")
        try:
            await manager.finish_user_run(run_id, final_status)
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"run {run_id!r} is not active"
            ) from None
        return FinishRunResponse(run_id=run_id, final_status=final_status)

    @router.get("/{run_id}/vocabulary")
    async def vocabulary(run_id: str) -> VocabularyResponse:
        session = _session_or_ambient(run_id)
        registry = session.manager.registry
        return VocabularyResponse(
            impls=[VocabularyEntry(name=name, description=spec.description)
                   for name, spec in sorted(registry.impls.items())],
            tools=[VocabularyEntry(name=name, description=spec.description)
                   for name, spec in sorted(registry.tools.items())],
            composites=[VocabularyEntry(name=name,
                                        description=spec.description)
                        for name, spec in sorted(
                            registry.composites.items())],
        )

    return router