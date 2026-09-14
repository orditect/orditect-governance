"""Event-type vocabulary hook.

Audit event types are BUSINESS-OWNED (T6-neutral): the component layer
never defines vocabulary such as "tool_call" or "memory_call". It only
requires that every governed call carries an event type string chosen
by the business layer. This module exists as the single import point
documenting that contract.
"""

from __future__ import annotations

EventType = str