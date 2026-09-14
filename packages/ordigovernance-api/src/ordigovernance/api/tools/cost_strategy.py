"""Cost pricing strategies for governed tool calls.

The cost function receives the handler result and returns budget units.
Flat per-call pricing is the reference strategy; token/result-based
pricing is a valid business alternative.
"""

from __future__ import annotations

from typing import Any, Callable

CostFn = Callable[[Any], int]


def flat_cost(units: int = 1) -> CostFn:
    """Charge a fixed number of budget units per call."""

    def cost(_result: Any) -> int:
        return units

    return cost