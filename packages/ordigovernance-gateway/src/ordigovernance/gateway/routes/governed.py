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
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ordigovernance.api.naming import make_call_id
from ordigovernance.api.tools import check_reserved_payload_keys

from ordigovernance.gateway.schemas import (
    LlmChatRequest,
    LlmChatResponse,
    ToolCallRequest,
    ToolCallResponse,
)

log = logging.getLogger(__name__)


@dataclass
class StreamChunkData:
    """Fields extracted from one raw governed-client stream chunk.

    Opened for the /v1 OpenAI-compat surface: tool_calls deltas and
    finish_reason must pass through for streaming tool-calling rounds.
    """

    text: str
    reasoning: str
    tool_calls: list | None = None
    finish_reason: str | None = None
    usage: dict | None = None


def _delta_tool_calls(delta: dict) -> list | None:
    """tool_calls entries of one OpenAI-shaped delta, when present."""
    calls = delta.get("tool_calls")
    return calls if isinstance(calls, list) else None


def parse_stream_chunk(chunk: Any) -> StreamChunkData:
    """Parse one raw stream chunk a governed client may forward.

    Tolerates the observed shapes: plain {"delta": str}, OpenAI-shaped
    deltas ({"delta": {...}} or {"choices": [{"delta": {...}}]}), the
    include_usage tail chunk ({"choices": [], "usage": {...}}), and the
    orditect framework's SourceChunk objects (attribute access:
    text / thinking; the terminal marker chunk reads as empty).
    """
    if isinstance(chunk, dict):
        usage = (chunk.get("usage")
                 if isinstance(chunk.get("usage"), dict) else None)
        delta = chunk.get("delta")
        if isinstance(delta, str):
            return StreamChunkData(text=delta, reasoning="", usage=usage)
        if isinstance(delta, dict):
            reasoning = (delta.get("reasoning_content")
                         or delta.get("reasoning") or "")
            return StreamChunkData(
                text=delta.get("content") or "",
                reasoning=reasoning,
                tool_calls=_delta_tool_calls(delta),
                usage=usage,
            )
        choices = chunk.get("choices") or []
        if choices:
            first = choices[0] or {}
            inner = first.get("delta") or {}
            if isinstance(inner, dict):
                reasoning = (inner.get("reasoning_content")
                             or inner.get("reasoning") or "")
                return StreamChunkData(
                    text=inner.get("content") or "",
                    reasoning=reasoning,
                    tool_calls=_delta_tool_calls(inner),
                    finish_reason=first.get("finish_reason"),
                    usage=usage,
                )
        return StreamChunkData(text="", reasoning="", usage=usage)
    if isinstance(chunk, str):
        return StreamChunkData(text=chunk, reasoning="")
    # orditect SourceChunk (and any duck-typed equivalent): text is the
    # content delta, thinking is the reasoning delta; the TERMINAL
    # marker chunk carries None on both (finish=True) and reads empty.
    if hasattr(chunk, "text") or hasattr(chunk, "thinking"):
        text = getattr(chunk, "text", None)
        thinking = getattr(chunk, "thinking", None)
        return StreamChunkData(
            text=text if isinstance(text, str) else "",
            reasoning=thinking if isinstance(thinking, str) else "",
        )
    return StreamChunkData(text=str(chunk), reasoning="")


def translate_call_failure(e: Exception):
    """Uniform governed-call failure translation (shared surfaces).

    Module-level so the /v1 OpenAI-compat surface reuses the exact
    same timeout / quota / vocabulary verdicts.
    """
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


def _sse_frame(payload: dict) -> str:
    """One SSE frame: a single JSON object on a data: line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
            raise translate_call_failure(e) from None
        usage = response.get("usage") if isinstance(response, dict) else None
        return LlmChatResponse(
            status="ok", call_id=call_id, response=response, usage=usage)

    @router.post("/llm-chat-stream")
    async def llm_chat_stream(req: LlmChatRequest):
        """Stream one governed LLM call as SSE frames.

        Frame vocabulary (one JSON object per ``data:`` line):
          {"type": "delta", "text": str, "reasoning": str}
          {"type": "done", "call_id": str, "usage": {...} | None}
          {"type": "error", "detail": str}
        The done frame terminates the stream. Governance (semaphore,
        budget, audit, call_id idempotency) applies inside the client's
        stream() exactly as on the non-streaming path; include_usage is
        forced on so the done frame carries real token usage. No
        step_timeout wrapper: an idle transport abort on the consumer
        side governs stalled streams.
        """
        session = _session(req.run_id)
        client = session.llms.get(req.client)
        if client is None:
            raise HTTPException(
                status_code=422,
                detail=f"unknown llm client {req.client!r}; "
                       f"registered: {sorted(session.llms)}")
        stream_fn = getattr(client, "stream", None)
        if stream_fn is None:
            raise HTTPException(
                status_code=422,
                detail=f"llm client {req.client!r} does not expose "
                       f"stream(); use the non-streaming llm-chat "
                       f"endpoint")
        try:
            task_id, eid, seq = await session.allocate_call_identity(
                req.task_id, req.purpose)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown task {req.task_id!r} in run "
                       f"{session.run_id!r}") from None
        call_id = make_call_id(req.purpose, task_id, eid, seq=seq)
        kwargs = dict(req.kwargs)
        kwargs.setdefault("include_usage", True)

        async def event_gen():
            usage: dict | None = None
            try:
                async for chunk in client.stream(
                        messages=req.messages, call_id=call_id, **kwargs):
                    parsed = parse_stream_chunk(chunk)
                    if parsed.usage is not None:
                        usage = parsed.usage
                    if parsed.text or parsed.reasoning:
                        yield _sse_frame(
                            {"type": "delta", "text": parsed.text,
                             "reasoning": parsed.reasoning})
                yield _sse_frame({"type": "done", "call_id": call_id,
                                  "usage": usage})
            except Exception as e:
                yield _sse_frame({"type": "error",
                                  "detail": f"{type(e).__name__}: {e}"})

        return StreamingResponse(
            event_gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"},
        )

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
            raise translate_call_failure(e) from None
        return ToolCallResponse(
            status="ok", call_id=call_id, result=result, origin="executed")

    return router