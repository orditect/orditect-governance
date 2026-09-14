"""Side-effect typing, call classes, and the replay policy vocabulary.

Two orthogonal classification axes:

  Axis A - tool side-effect tag (declared at registration time):
      readonly | external | internal. What the handler does to the
      world.

  Axis B - call class (declared at the call site):
      llm | readonly | producer | internal. What kind of traffic one
      memoized call is.

  - llm:      non-deterministic inference (the measured variable).
  - readonly: world reads (the controlled variable).
  - producer: output-producing calls; refuse every policy override.
  - internal: evidence-chain writes; not configurable.

Replay policy table shape:
    {
      "llm":      "always" | "on_resume" | "never",
      "readonly": "always" | "on_resume" | "never",
      "external": "stub" | "sandbox" | "allow",
      "purpose:<purpose>": <mode legal for that call's class>,
    }

This module carries the VOCABULARY only. The resolution chain that
turns a policy table into per-call routing decisions is an
engine-level concern and lives outside this package.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal


class SideEffect(str, Enum):
    READONLY = "readonly"
    EXTERNAL = "external"
    INTERNAL = "internal"


class CallClass(str, Enum):
    LLM = "llm"
    READONLY = "readonly"
    PRODUCER = "producer"
    INTERNAL = "internal"


STUB = "stub"
SANDBOX = "sandbox"
ALLOW = "allow"

EXTERNAL_MODES = frozenset({STUB, SANDBOX, ALLOW})
REUSE_MODES = frozenset({"always", "on_resume", "never"})

ReusePolicy = Literal["always", "on_resume", "never"]


def normalize_side_effect(value: "SideEffect | str") -> SideEffect:
    """Coerce a registered side-effect tag to the enum."""
    if isinstance(value, SideEffect):
        return value
    try:
        return SideEffect(str(value))
    except ValueError:
        raise ValueError(
            f"unknown side_effect {value!r}; "
            f"expected one of {[s.value for s in SideEffect]}"
        ) from None