"""GovernedSource: LLMSourceProtocol adapter over a governed client.

Migrated from the reference application's publish node and generalized.
Governance (semaphore / budget / audit / pointer-ization) stays entirely
inside the client; this adapter only reshapes the runner's source
request into the client's stream() call.

include_usage=True lets endpoints that accept stream_options report a
usage tail chunk, so streaming calls are billed by real tokens.
include_usage=False remains the fallback for endpoints that reject or
mishandle stream_options (since the bridge fix, such endpoints fail
loudly with StreamEmptyError instead of silently returning an empty
body); pair it with zero_cost_on_none so usage-less calls price at 0
instead of crashing.

The request object is used structurally: any object exposing a
.payload dict with "messages" and an optional "call_id" works.
"""
from __future__ import annotations

from typing import Any

from ordigovernance.runtime.stream.cancel_adapter import AsyncCancelToken


class GovernedSource:
    """Adapt one governed streaming client to the runner's source shape."""

    def __init__(self, llm: Any, *, include_usage: bool = False) -> None:
        self._llm = llm
        self._include_usage = include_usage

    async def stream(self, request: Any, cancel_token: Any = None):
        payload = getattr(request, "payload", None) or {}
        async for chunk in self._llm.stream(
            messages=payload["messages"],
            call_id=payload.get("call_id"),
            cancel_token=(
                AsyncCancelToken(cancel_token)
                if cancel_token is not None
                else None
            ),
            include_usage=self._include_usage,
        ):
            yield chunk