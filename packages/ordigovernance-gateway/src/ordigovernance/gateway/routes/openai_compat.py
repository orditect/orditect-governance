"""OpenAI-compatible surface over the governed call plane (D18).

A thin protocol envelope over the SAME governed LLM path as
POST /governed/llm-chat: OpenAI chat-completions bodies in, OpenAI
chat-completions bodies out. Governance is unchanged underneath --
semaphores, budget, call_id idempotency, audit events and the D8
identity discipline all apply exactly as on the governed-native
surface. OpenAI-compatible clients (n8n's built-in OpenAI Chat Model
node, the openai SDKs, any OpenAI-gateway consumer) can point at this
gateway without a custom integration node.

Attribution (priority order):
  1. X-Governance-Run-Id / X-Governance-Task-Id / X-Governance-Purpose
     headers (clients that can configure custom request headers);
  2. the "@active" sentinel in the run header resolves, PER REQUEST,
     to the currently active user run -- a credential-static header
     with runtime-dynamic resolution, so one credential survives run
     turnover (the single-active-run guard otherwise turns a fixed run
     id into a guaranteed 404 on the second execution);
  3. absent headers route to the ambient run (D2: the formal container
     for attribution-less traffic).

Mapping rules:
  - body.model       -> client (a session.llms registry key; GET
                        /v1/models lists the vocabulary)
  - body.messages    -> passed through verbatim
  - body.stream / body.stream_options -> transport handling in this
                        shell (stream_options.include_usage is
                        translated to the include_usage kwarg)
  - every other body field (tools, tool_choice, temperature, stop,
    user, ...) is transported OPAQUELY as kwargs -- the same dict the
    governed client forwards to the endpoint.

Streaming: OpenAI chunk envelopes (chat.completion.chunk) carrying
content / reasoning_content / tool_calls deltas, a final chunk with
finish_reason "stop" plus usage, and a "data: [DONE]" terminator.

Errors are returned in the OpenAI error envelope
({"error": {"message", "type"}}) so OpenAI-native SDKs surface them
readably. Retry disclosure: OpenAI SDKs may retry transient failures;
each HTTP retry is a fresh governed call (call_id idempotency does not
span retries), so a retried request may be billed twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse

from ordigovernance.api.naming import make_call_id
from ordigovernance.gateway.routes.governed import (
    parse_stream_chunk,
    translate_call_failure,
)

log = logging.getLogger(__name__)

DEFAULT_PURPOSE = "openai-compat"
ACTIVE_RUN_SENTINEL = "@active"
AMBIENT_RUN_SENTINEL = "ambient"

# Request-body keys this shell consumes instead of forwarding:
#   model           -> mapped to the client registry key
#   messages        -> passed to client.chat/stream verbatim
#   stream          -> selects the transport branch
#   stream_options  -> translated to the include_usage kwarg
_CONSUMED_KEYS = ("model", "messages", "stream", "stream_options")

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


class OpenAICompatError(Exception):
    """A compat-surface error carrying an OpenAI error response."""

    def __init__(self, status: int, message: str,
                 err_type: str = "invalid_request_error") -> None:
        self.response = JSONResponse(
            status_code=status,
            content={"error": {"message": message, "type": err_type}},
        )
        super().__init__(message)


def _compat_error(status: int, message: str,
                  err_type: str = "invalid_request_error") -> None:
    raise OpenAICompatError(status, message, err_type)


def _sse_frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk_envelope(call_id: str, model: str, delta: dict, *,
                    finish_reason: str | None = None,
                    usage: dict | None = None) -> dict:
    out = {
        "id": call_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    if usage is not None:
        out["usage"] = usage
    return out


def _resolve_session(manager, run_header: str | None):
    """Resolve the session for one compat request (D18 attribution).

    None header          -> ambient run (D2 catch-all).
    "@active" sentinel   -> the currently active user run; degrades to
                           ambient when none is active (a static
                           credential header must not break calls
                           between runs).
    "ambient" (explicit) -> ambient run (tests / diagnostics).
    any other value      -> strict resolution; unknown or inactive
                           runs fail loudly with 404.
    """
    if run_header is None:
        return manager.ambient
    if run_header == ACTIVE_RUN_SENTINEL:
        return manager.active or manager.ambient
    if run_header == AMBIENT_RUN_SENTINEL:
        return manager.ambient
    try:
        return manager.resolve(run_header)
    except KeyError:
        _compat_error(
            404,
            f"unknown or inactive run {run_header!r}; use "
            f"'{ACTIVE_RUN_SENTINEL}' to attribute to the active run, "
            f"or omit the header to route to the ambient run")


def build_openai_compat_router(
    get_manager,
    *,
    auth_dependency: Any = None,
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the /v1 OpenAI-compatible router.

    Auth is the gateway bearer token (the OpenAI ecosystem convention:
    clients send it as the API key). The ambient session's client
    registry defines the model vocabulary (settings-level
    make_clients: every session resolves the same client names).
    """
    router = APIRouter(tags=tags or ["openai-compat"],
                       dependencies=([Depends(auth_dependency)]
                                     if auth_dependency else []))


    @router.get("/v1/models")
    async def list_models() -> dict:
        """The model vocabulary: one entry per registered client name.

        n8n's OpenAI credential test and the Chat Model node's model
        dropdown both read this endpoint, so the response shape must
        match the OpenAI models list protocol exactly.
        """
        ambient = get_manager().ambient
        if ambient is None:
            _compat_error(503, "gateway hot path not ready",
                          "server_error")
        return {
            "object": "list",
            "data": [
                {
                    "id": name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "ordigovernance",
                }
                for name in sorted(ambient.llms)
            ],
        }

    @router.post("/v1/chat/completions")
    async def chat_completions(
            body: dict = Body(...),
            x_run_id: str | None = Header(
                default=None, alias="X-Governance-Run-Id"),
            x_task_id: str | None = Header(
                default=None, alias="X-Governance-Task-Id"),
            x_purpose: str | None = Header(
                default=None, alias="X-Governance-Purpose"),
    ):
        """One governed LLM call in the OpenAI chat-completions shape."""
        session = _resolve_session(get_manager(), x_run_id)

        model = str(body.get("model") or "")
        client = session.llms.get(model)
        if client is None:
            _compat_error(
                422,
                f"unknown model {model!r}; registered: "
                f"{sorted(session.llms)} (GET /v1/models)")

        purpose = x_purpose or DEFAULT_PURPOSE
        # D8 identity on the SAME path as /governed/llm-chat: a known
        # task reuses the hot record's eid; a task unknown to the run
        # is a 404; an omitted task mints an ephemeral identity.
        try:
            task_id, eid, seq = await session.allocate_call_identity(
                x_task_id, purpose)
        except KeyError:
            _compat_error(404, f"unknown task {x_task_id!r} in run "
                               f"{session.run_id!r}")
        call_id = make_call_id(purpose, task_id, eid, seq=seq)

        kwargs = {k: v for k, v in body.items() if k not in _CONSUMED_KEYS}
        messages = body.get("messages") or []
        stream = bool(body.get("stream"))
        # OpenAI stream_options.include_usage -> the governed client's
        # include_usage kwarg (the usage tail chunk). Streaming only:
        # the non-streaming chat() does not take the flag.
        if stream and isinstance(body.get("stream_options"), dict) \
                and body["stream_options"].get("include_usage"):
            kwargs["include_usage"] = True

        if stream:
            stream_fn = getattr(client, "stream", None)
            if stream_fn is None:
                _compat_error(
                    422,
                    f"model {model!r} does not expose stream(); retry "
                    f"without \"stream\": true")

            async def event_gen():
                usage: dict | None = None
                try:
                    async for chunk in client.stream(
                            messages=messages, call_id=call_id, **kwargs):
                        parsed = parse_stream_chunk(chunk)
                        if parsed.usage is not None:
                            usage = parsed.usage
                        delta: dict = {}
                        if parsed.text:
                            delta["content"] = parsed.text
                        if parsed.reasoning:
                            delta["reasoning_content"] = parsed.reasoning
                        if parsed.tool_calls:
                            delta["tool_calls"] = parsed.tool_calls
                        if delta or parsed.finish_reason:
                            yield _sse_frame(_chunk_envelope(
                                call_id, model, delta,
                                finish_reason=parsed.finish_reason))
                    yield _sse_frame(_chunk_envelope(
                        call_id, model, {}, finish_reason="stop",
                        usage=usage))
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    yield _sse_frame({"error": {
                        "message": f"{type(e).__name__}: {e}",
                        "type": "gateway_error"}})

            return StreamingResponse(
                event_gen(), media_type="text/event-stream",
                headers=_SSE_HEADERS)

        try:
            response = await asyncio.wait_for(
                client.chat(messages=messages, call_id=call_id, **kwargs),
                timeout=session.manager.settings.step_timeout,
            )
        except Exception as e:
            raise translate_call_failure(e) from None
        if not isinstance(response, dict) or "choices" not in response:
            # Fail loudly on shape drift instead of silently returning
            # an empty reply: the /v1 surface requires OpenAI-shaped
            # clients in the registry.
            _compat_error(
                502,
                f"governed client {model!r} returned a response without "
                f"'choices' (got: "
                f"{sorted(response) if isinstance(response, dict) else type(response).__name__}); "
                f"the /v1 surface requires OpenAI-shaped clients in the "
                f"registry -- check GATEWAY_MODEL_CLIENTS",
                "server_error")
        return {
            "id": call_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": response["choices"],
            "usage": response.get("usage"),
        }

    return router