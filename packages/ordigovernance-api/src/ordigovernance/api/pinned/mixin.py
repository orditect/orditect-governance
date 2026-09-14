"""PinnedInputMixin: replay pin injection for governed tasks/agents.

Semantics (D1): pinned_input is an OPAQUE dict supplied by the replay
channel — either resolved from the archive (pinning a historical
generation) or given explicitly (probing behavior over new input). The
component layer never inspects its keys; the business impl interprets
them.

A pinned generation still archives itself and still emits governed
calls: pinning replaces INPUT acquisition, never governance.
"""

from __future__ import annotations

from typing import Any


class PinnedInputMixin:
    """Adds pinned-input plumbing to a task/agent implementation.

    Cooperative multiple inheritance: passes *args/**kwargs through.
    """

    def __init__(self, *args: Any, pinned_input: dict | None = None,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pinned_input = pinned_input

    @property
    def pinned_input(self) -> dict | None:
        return self._pinned_input

    @property
    def is_pinned(self) -> bool:
        return self._pinned_input is not None