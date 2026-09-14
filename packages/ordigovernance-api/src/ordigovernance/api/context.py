"""Engine plug-in protocols for the runtime AgentContext.

The runtime ships mechanism-direct defaults (passthrough). Engines
with richer semantics (memo reuse across generations, replay policy
routing) implement these protocols and are injected at assembly time;
agent and business code never changes across tiers.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from ordigovernance.api.side_effect import CallClass, SideEffect

@runtime_checkable
class MemoLayerProtocol(Protocol):
    """Structural contract of one generation's memo layer."""

    async def get_or_execute(
        self,
        purpose: str,
        seq: int | str,
        inputs: dict,
        execute: Callable[[], Awaitable[dict]],
        *,
        reuse: str = "always",
        origin_seq: int | str | None = None,
        mode: str | None = None,
    ) -> tuple[dict, str]:
        """Return (result, origin): "executed" | "reused:<call_id>" | "ungoverned"."""
        ...


@runtime_checkable
class PolicyResolverProtocol(Protocol):
    """Structural contract of one generation's policy resolver."""

    @property
    def active(self) -> bool:
        """Whether any replay routing is in force."""
        ...

    @property
    def table(self) -> dict:
        """The policy table as given (read-only snapshot)."""
        ...

    def resolve(
        self,
        purpose: str,
        category: CallClass,
        *,
        side_effect: SideEffect = SideEffect.READONLY,
        declared: str = "always",
    ) -> str:
        """Resolve the effective mode for one call site."""
        ...