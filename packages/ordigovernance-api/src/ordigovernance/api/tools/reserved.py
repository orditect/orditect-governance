
"""Reserved payload keys for governed tool calls.

Caller kwargs travel as the tool payload through the governed call
plumbing, so they must never collide with the plumbing's own keyword
parameters (inputs / reuse / record_origin at the tracked-atom layer;
purpose / seq / params / call_id / content_fn / payload_fn at the
governed-call layer). The runtime atoms and the gateway enforce the
same list; it lives in the api package because it is shared plumbing
vocabulary, not implementation.
"""

from __future__ import annotations

RESERVED_PAYLOAD_KEYS = frozenset({
    "name", "inputs", "reuse", "record_origin",
    "purpose", "seq", "params", "call_id", "content_fn", "payload_fn",
})


def check_reserved_payload_keys(tool_name: str, inputs: dict) -> None:
    """Fail loudly when payload keys collide with the governed plumbing."""
    collision = RESERVED_PAYLOAD_KEYS & set(inputs)
    if collision:
        raise ValueError(
            f"tool {tool_name!r} payload keys {sorted(collision)} collide "
            f"with the governed call plumbing; rename the tool parameter "
            f"(reserved: {sorted(RESERVED_PAYLOAD_KEYS)})"
        )