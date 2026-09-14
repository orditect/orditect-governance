"""ScriptedLLMClient: deterministic fake LLM for controlled experiments.

The controlled light source for drift-attribution tests and any bridge
contract suite: responses are served from a content-addressed script
(no randomness anywhere), call counts are recorded per purpose, and
set_version() flips the response script to simulate model drift
between replay generations.

The client satisfies the component LLM protocols (chat + stream) so it
drops into any AgentContext llm registry.
"""

from __future__ import annotations

import hashlib
from typing import Any, AsyncIterator


def _stable_int(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


class ScriptedLLMClient:
    """Deterministic chat/stream fake with version-switchable scripts.

    Calls are recorded verbatim (call_id, messages, kwargs) so tests
    can assert on routing, parameter merging and call counts.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._version = 0

    def set_version(self, version: int) -> None:
        """Switch the response script (simulates LLM drift)."""
        self._version = version

    @property
    def call_count(self) -> int:
        return len([c for c in self.calls if not c.get("stream")])

    def _content(self, messages: list[dict]) -> str:
        blob = repr(messages)
        seed = _stable_int(f"{self._version}:{blob}") % 997
        return f"response-v{self._version}-{seed}"

    async def chat(self, messages: list[dict], *, call_id: str,
                   **kwargs: Any) -> dict:
        self.calls.append({"call_id": call_id, "messages": messages,
                           "kwargs": dict(kwargs)})
        return {
            "choices": [{"message": {
                "role": "assistant",
                "content": self._content(messages)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
        }

    async def stream(self, messages: list[dict], *, call_id: str,
                     **kwargs: Any) -> AsyncIterator[dict]:
        self.calls.append({"call_id": call_id, "messages": messages,
                           "kwargs": dict(kwargs), "stream": True})
        content = self._content(messages)
        for i in range(0, len(content), 8):
            yield {"delta": content[i:i + 8]}