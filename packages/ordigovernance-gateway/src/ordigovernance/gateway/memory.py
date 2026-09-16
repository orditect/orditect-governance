"""Default memo/archive body for the gateway (D13).

The gateway ships its own body implementations instead of reusing
the testing package's mock: production paths never run on test
fixtures. Two backends:

  - RedisMemoryBody: one hash per deployment (production);
  - DictMemoryBody: process-local (memory mode, zero infrastructure).

Both return ENVELOPES, never a bare None: the framework's cancelled
verdict keys on `result is None` (docs/pitfalls.md 13.10).
"""

from __future__ import annotations

import json
from typing import Any


class RedisMemoryBody:
    """Redis-backed memo/archive body (one hash per deployment)."""

    def __init__(self, client: Any, *, key: str = "ordigw:memo") -> None:
        self._client = client
        self._key = key

    async def read(self, key: str) -> dict:
        raw = await self._client.hget(self._key, key)
        return {"key": key, "value": json.loads(raw) if raw else None}

    async def write(self, key: str, value: dict) -> dict:
        await self._client.hset(
            self._key, key, json.dumps(value, ensure_ascii=False))
        return {"key": key, "stored": True}


class DictMemoryBody:
    """Process-local memo/archive body (memory mode)."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    async def read(self, key: str) -> dict:
        return {"key": key, "value": self._data.get(key)}

    async def write(self, key: str, value: dict) -> dict:
        self._data[key] = value
        return {"key": key, "stored": True}