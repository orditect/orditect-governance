"""LLM client protocols (B-class atoms).

Any LLM behind a client is governable: the client itself carries the
call-level governance plane (semaphore resource, budget, audit writer,
content writer). The component layer therefore treats clients as opaque
B-class atoms and only pins the two invocation shapes observed from the
reference implementation:

  chat   - non-streaming completion returning the raw response dict
  stream - async iterator of chunks (used by the stream runner adapters)

D4: a single agent/task may hold SEVERAL clients (multi-model routing,
multi-reviewer quality gates). Clients are provided as a registry
dict[str, LLMClientProtocol] at assembly time; names are business
vocabulary and never interpreted by the component layer.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class LLMChatProtocol(Protocol):
    """Non-streaming governed chat call."""

    async def chat(self, messages: list[dict], *, call_id: str,
                   **kwargs: Any) -> dict:
        """Return the raw completion dict (OpenAI-shaped response)."""
        ...


@runtime_checkable
class LLMStreamProtocol(Protocol):
    """Streaming governed call; chunks are yielded as they arrive."""

    def stream(self, messages: list[dict], *, call_id: str,
               **kwargs: Any) -> AsyncIterator[Any]:
        ...


@runtime_checkable
class LLMClientProtocol(LLMChatProtocol, LLMStreamProtocol, Protocol):
    """Full client surface. Reference: orditect.bridge.openai.GovernedLLMClient."""
    ...