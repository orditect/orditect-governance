"""Cancellation token adapter.

Migrated from the reference application's publish node. The stream
runner's CancellationToken exposes a synchronous is_cancelled(); the
flow-side governed clients AWAIT token.is_cancelled(). Forwarding the
raw token raises TypeError there, so the source wraps it instead of
touching any bridge internals.
"""

from __future__ import annotations

import inspect
from typing import Any


class AsyncCancelToken:
    """Adapt a sync/async is_cancelled() token to the async duck type."""

    def __init__(self, token: Any) -> None:
        self._token = token

    async def is_cancelled(self) -> bool:
        fn = getattr(self._token, "is_cancelled", None)
        if fn is None:
            return False
        result = fn()
        if inspect.isawaitable(result):
            return bool(await result)
        return bool(result)