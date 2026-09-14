"""In-memory event fan-out plus JSON-safe value conversion.

Migrated verbatim from the reference application's runtime events
module. One bounded queue per subscriber; publishing never blocks
(slow consumers drop events instead of stalling the workflow).
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any


def jsonable(value: Any) -> Any:
    """Recursively convert a value to JSON-safe primitives.

    Handles Enum -> .value, objects exposing to_payload()/model_dump()/
    dict(), and containers. Unknown objects degrade to a copy of their
    instance dict (or str()) instead of raising: read paths must never
    crash consumers over a serialization edge case.
    """
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    for method in ("to_payload", "model_dump", "dict"):
        fn = getattr(value, method, None)
        if callable(fn):
            return jsonable(fn())
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return {str(k): jsonable(v) for k, v in vars(value).items()}
    return str(value)


class EventBus:
    """One bounded queue per subscriber; publishing never blocks."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop for slow consumers; never block the workflow.
                pass