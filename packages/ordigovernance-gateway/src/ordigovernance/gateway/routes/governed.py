"""Call-plane endpoints: governed llm chat and governed tool calls.

Identity discipline (D8): a given task_id must have a hot record
(404 otherwise); a missing task_id mints an ephemeral identity. Seq
slots are per (task_id, purpose), starting above the agent band.
Call ids always follow the naming discipline via make_call_id.

Timeout policy (uniform): a governed call exceeding step_timeout
surfaces as 504 (semaphore queue or handler); an exhausted budget
surfaces as 409; unknown vocabulary as 422 listing the valid names.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ordigovernance.api.naming import make_call_id
from ordigovernance.api.tools import check_reserved_payload_keys

from ordigovernance.gateway.schemas import (
    LlmChatRequest,
    LlmChatResponse,
    ToolCallRequest,
    ToolCallResponse,
)

log = logging.getLogger(__name__)


def build_governed_router(
    get_manager,
    *,
    auth_dependency: Any = None,
    prefix: str = "/governed",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the call-plane router over a SessionManager accessor."""
    router = APIRouter(prefix=prefix, tags=tags or ["governed"],
                       dependencies=([Depends(auth_dependency)]
                                     if auth_dependency else []))

    def _session(run_id: str | None):
        try:
            return get_manager().resolve(run_id)
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail=f"unknown run {e.args[0]!r}"
            ) from None

    def _translate_failure(e: Exception):
        text = f"{type(e).__name__}: {e}"
        lowered = text.lower()
        if isinstance(e, asyncio.TimeoutError) or "timed out" in lowered:
            return HTTPException(
                status_code=504,
                detail=f"governed call exceeded step timeout: {text}")
        if "limit" in lowered or "quota" in lowered or "budget" in lowered:
            return HTTPException(
                status_code=409,
                detail=f"admission denied (budget or quota): {text}")
        return HTTPException(status_code=500, detail=text)

    @router.post("/llm-chat")
    async def llm_chat(req: LlmChatRequest) -> LlmChatResponse:
        session = _session(req.run_id)
        client = session.llms.get(req.client)
        if client is None:
            raise HTTPException(
                status_code=422,
                detail=f"unknown llm client {req.client!r}; "
                       f"registered: {sorted(session.llms)}")
        try:
            task_id, eid, seq = await session.allocate_call_identity(
                req.task_id, req.purpose)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown task {req.task_id!r} in run "
                       f"{session.run_id!r}") from None
        call_id = make_call_id(req.purpose, task_id, eid, seq=seq)
        try:
            response = await asyncio.wait_for(
                client.chat(messages=req.messages, call_id=call_id,
                            **req.kwargs),
                timeout=session.manager.settings.step_timeout,
            )
        except Exception as e:
            raise _translate_failure(e) from None
        usage = response.get("usage") if isinstance(response, dict) else None
        return LlmChatResponse(
            status="ok", call_id=call_id, response=response, usage=usage)

    @router.post("/tool-call")
    async def tool_call(req: ToolCallRequest) -> ToolCallResponse:
        session = _session(req.run_id)
        try:
            task_id, eid, seq = await session.allocate_call_identity(
                req.task_id, req.tool)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown task {req.task_id!r} in run "
                       f"{session.run_id!r}") from None
        try:
            check_reserved_payload_keys(req.tool, req.inputs)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        try:
            tool_set = session.build_tool_set(task_id,
                                              tool_names=[req.tool])
        except KeyError:
            known = sorted(session.manager.registry.tools) \
                if session.manager.registry else []
            raise HTTPException(
                status_code=422,
                detail=f"unknown tool {req.tool!r}; registered: {known}"
            ) from None
        call_id = make_call_id(req.tool, task_id, eid, seq=seq)
        try:
            result = await asyncio.wait_for(
                tool_set.call(req.tool, call_id=call_id,
                              params=dict(req.inputs), **req.inputs),
                timeout=session.manager.settings.step_timeout,
            )
        except ValueError as e:
            # Reserved payload-key collisions fail loudly at the atom
            # boundary with a rename instruction (docs/pitfalls 14.4);
            # surface them as vocabulary errors, not server errors.
            raise HTTPException(status_code=422, detail=str(e)) from None
        except Exception as e:
            raise _translate_failure(e) from None
        return ToolCallResponse(
            status="ok", call_id=call_id, result=result, origin="executed")

    return router