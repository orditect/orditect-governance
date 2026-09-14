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
        """One governed tool call; no memo wrap."""
        self._seq += 1
        result = await self._ctx.tool_call(
            name, *args, purpose=name, seq=self._seq,
            params=dict(inputs or kwargs), **kwargs,
        )
        if record_origin:
            self._ctx.record_origin(name, "executed")
        return result