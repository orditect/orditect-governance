"""MemoBackend: the minimal governed key-value IO memo/archive need.

Structural contract (observed from the reference implementation):
    memory_read(key, call_id=...)         -> {"key": ..., "value": ...} | None
    memory_write(key, value, call_id=...) -> dict

Implementations MUST route every call through the call-level governance
plane (semaphore / budget / audit / pointer-ization): every memo hit and
every archive write is an audit-visible governed call. The reference
implementation is the GovernedToolSet facade in the business layer.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class MemoBackend(Protocol):
    async def memory_read(self, key: str, *, call_id: str,
                          payload_fn: Callable[[Any], dict] | None = None
                          ) -> dict | None:
        """Governed read; returns the stored envelope (value may be None).

        payload_fn: optional audit-payload injector forwarded to the
        underlying governed call (the memget decision field is the
        reference use); None for plain reads.
        """
        ...

    async def memory_write(self, key: str, value: Any, *, call_id: str,
                           payload_fn: Callable[[Any], dict] | None = None
                           ) -> dict:
        """Governed write; returns the storage acknowledgement.

        payload_fn: optional audit-payload injector forwarded to the
        underlying governed call (the memput content-identity fields
        are the reference use); None for plain writes. Implementations
        predating this parameter are tolerated by the memo layer,
        which probes for support before passing it.
        """
        ...