"""Tracked-atom protocols: the bridge-facing consumption surface.

A tracked atom is a per-generation object: the generation's
governance context is bound at CONSTRUCTION time, so invocation
signatures carry no governance identity. Bridges adapt framework
message/tool formats to these surfaces; engines (memo reuse, replay
policy routing) plug in behind the same protocols without any bridge
change.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class TrackedLLMProtocol(Protocol):
    """The full LLM-atom surface bridges consume."""

    @property
    def client_name(self) -> str:
        """Registry name of the underlying client."""
        ...

    async def complete(self, messages: list[dict], **kwargs: Any) -> dict:
        """One governed non-streaming call; returns the raw response dict."""
        ...

    def stream(self, messages: list[dict], **kwargs: Any) -> AsyncIterator[Any]:
        """One governed streaming call; yields chunks as they arrive."""
        ...


@runtime_checkable
class TrackedToolSetProtocol(Protocol):
    """The full tool-atom surface bridges consume."""

    async def call(
        self,
        name: str,
        *args: Any,
        inputs: dict | None = None,
        reuse: str = "always",
        record_origin: bool = True,
        **kwargs: Any,
    ) -> Any:
        """One governed tool call (memo-wrapped behind engine layers).

        inputs: the memo-key payload (defaults to kwargs).
        reuse:  mechanism vocabulary on the passthrough tier
                ("always"/"never" both execute; "never" is the
                producer alias); engine tiers give the modes their
                routing semantics.
        record_origin: when False, the call skips the origins record.
        """
        ...