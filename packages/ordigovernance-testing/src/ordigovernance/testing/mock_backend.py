"""HandlerBackendAdapter: a MemoBackend surface over handler-shaped functions.

The mock memory handlers (mock_tools.memory_read / memory_write) are
plain async callables with (key) and (key, value) signatures.
Governance surfaces (the generation-content router, load_generation,
drift evidence reads) speak the MemoBackend protocol instead: keyword
call_id, an optional payload_fn audit-payload injector, and envelope
results. Handing a bare module over as the backend fails three frames
away with a TypeError on the keyword surface; this adapter bridges the
two shapes explicitly.

payload_fn discipline: the adapter APPLIES payload_fn and discards its
return value. There is no audit stream behind a fixture backend, but
applying it keeps the payload function's total-on-every-outcome
contract exercised in tests.
"""

from __future__ import annotations

from typing import Any, Callable

from ordigovernance.api.memo import MemoBackend


class HandlerBackendAdapter(MemoBackend):
    """Wrap handler-shaped read/write callables as a MemoBackend."""

    def __init__(self, read_handler: Callable,
                 write_handler: Callable) -> None:
        self._read = read_handler
        self._write = write_handler

    async def memory_read(self, key: str, *, call_id: str,
                          payload_fn: Callable[[Any], dict] | None = None
                          ) -> dict | None:
        result = await self._read(key)
        if payload_fn is not None:
            payload_fn(result)
        return result

    async def memory_write(self, key: str, value: Any, *, call_id: str,
                           payload_fn: Callable[[Any], dict] | None = None
                           ) -> dict:
        ack = await self._write(key, value)
        if payload_fn is not None:
            payload_fn(ack)
        return ack