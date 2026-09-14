"""Pricing fallback for streaming endpoints (A5).

Some endpoints silently return an empty body when stream_options is
present, so the client's usage parser yields None. Pair the client's
cost_fn with this wrapper so such calls price at 0 instead of raising.
"""

from __future__ import annotations

from typing import Any, Callable


def zero_cost_on_none(cost_fn: Callable[[Any], int]) -> Callable[[Any], int]:
    """Price a None usage/result payload at 0; delegate otherwise."""

    def wrapped(result: Any) -> int:
        if result is None:
            return 0
        return cost_fn(result)

    return wrapped