"""Tool handler protocol (A-class atoms).

A handler is any pre-existing async callable the business layer wants
governed. The component layer wraps it without modification behind a
GovernedCallClient; handler signatures stay business-owned.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

ToolHandler = Callable[..., Awaitable[Any]]