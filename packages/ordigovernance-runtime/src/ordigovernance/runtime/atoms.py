"""Passthrough tracked atoms: mechanism-direct A/B atoms for the
runtime tier.

Every call is a real governed call (semaphore / budget / audit /
pointer-ization all apply), just without memo reuse or replay policy
routing. Engine implementations (memo reuse, policy routing) plug in
behind the same api protocols at assembly time; bridges and agent code
never change across tiers.
"""

from __future__ import annotations

from typing import Any

from ordigovernance.api.atoms import TrackedLLMProtocol, TrackedToolSetProtocol
from ordigovernance.api.naming import SEQ_AGENT_BASE

# Payload keys that collide with the governed call plumbing when they
# travel through **kwargs: silent capture at this signature (inputs /
# reuse / record_origin) or a confusing TypeError several frames down
# (purpose / seq / params / call_id / content_fn / payload_fn). A
# business tool whose real parameter uses one of these names must be
# renamed before registration.
_RESERVED_PAYLOAD_KEYS = frozenset({
    "name", "inputs", "reuse", "record_origin",
    "purpose", "seq", "params", "call_id", "content_fn", "payload_fn",
})

class PassthroughTrackedLLM(TrackedLLMProtocol):
    """Mechanism-direct LLM atom: governed, non-memoized."""

    def __init__(self, ctx, client: str, *, purpose: str = "agent-llm") -> None:
        self._ctx = ctx
        self._client = client
        self._purpose = purpose
        self._seq = SEQ_AGENT_BASE

    @property
    def client_name(self) -> str:
        return self._client

    async def complete(self, messages: list[dict], **kwargs: Any) -> dict:
        """One governed non-streaming call; no memo wrap."""
        self._seq += 1
        return await self._ctx.llm_call(
            self._client, self._purpose, self._seq,
            messages=messages, **kwargs,
        )

    async def stream(self, messages: list[dict], **kwargs: Any):
        """One governed streaming call; never memoized."""
        self._seq += 1
        async for chunk in self._ctx.llm_stream(
                self._client, self._purpose, self._seq,
                messages=messages, **kwargs,
        ):
            yield chunk


class PassthroughTrackedToolSet(TrackedToolSetProtocol):
    """Mechanism-direct tool atom: governed, non-memoized."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._seq = SEQ_AGENT_BASE

    async def call(
            self,
            name: str,
            *args: Any,
            inputs: dict | None = None,
            reuse: str = "always",
            record_origin: bool = True,
            **kwargs: Any,
    ) -> Any:
        """One governed tool call; no memo wrap.

        Payload keys travel through **kwargs to the handler, so they
        must not collide with the governed plumbing (see
        _RESERVED_PAYLOAD_KEYS); a collision fails loudly here with a
        rename instruction instead of silently corrupting routing or
        surfacing as a TypeError frames away from the cause.
        """
        collision = _RESERVED_PAYLOAD_KEYS & set(kwargs)
        if collision:
            raise ValueError(
                f"tool {name!r} payload keys {sorted(collision)} collide "
                f"with the governed call plumbing; rename the tool "
                f"parameter (reserved: {sorted(_RESERVED_PAYLOAD_KEYS)})"
            )
        self._seq += 1
        result = await self._ctx.tool_call(
            name, *args, purpose=name, seq=self._seq,
            params=dict(inputs or kwargs), **kwargs,
        )
        if record_origin:
            self._ctx.record_origin(name, "executed")
        return result