"""Content pointer-ization helpers (T5).

Outgoing call parameters are serialized into the content-addressed
store so every audit event carries a resolvable pointer to the exact
inputs of the call.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def params_content_fn(params: dict) -> Callable[[Any], bytes]:
    """Build a content_fn that pointer-izes the given call parameters."""

    def fn(_result: Any) -> bytes:
        return json.dumps(params, ensure_ascii=False).encode("utf-8")

    return fn