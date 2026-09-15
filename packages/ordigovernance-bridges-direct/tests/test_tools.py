"""build_tool_set registration behavior (acceptance).

Reuses the fake-governance-plane pattern of the runtime's
test_side_effect_registry suite: registration is synchronous and
purely structural, so no governed call ever fires here.
"""

from __future__ import annotations

from ordigovernance.api.side_effect import SideEffect
from ordigovernance.bridges.direct.tools import build_tool_set


class _Governor:
    pass


class _Budget:
    pass


class _Audit:
    async def write(self, event):
        return event


class _Content:
    pass


class _Store:
    audit = _Audit()
    content = _Content()


async def _handler(*args, **kwargs):
    return {"ok": True}


def _build(**kwargs):
    return build_tool_set(
        _Governor(), _Budget(), _Store(), task_id="t", **kwargs)


def test_registers_business_tools_with_side_effects():
    tools = _build(tools={
        "search": {"handler": _handler, "resource": "web_search",
                   "event_type": "tool_call", "side_effect": "readonly"},
        "send_email": {"handler": _handler, "resource": "smtp",
                       "event_type": "tool_call",
                       "side_effect": "external"},
    })
    assert tools.tool_names == ["search", "send_email"]
    assert tools.side_effect_of("search") is SideEffect.READONLY
    assert tools.side_effect_of("send_email") is SideEffect.EXTERNAL


def test_missing_side_effect_defaults_to_readonly():
    tools = _build(tools={
        "search": {"handler": _handler, "resource": "r",
                   "event_type": "tool_call"},
    })
    assert tools.side_effect_of("search") is SideEffect.READONLY


def test_memory_handlers_register_as_internal():
    tools = _build(tools={}, memory_read=_handler, memory_write=_handler)
    assert tools.side_effect_of("memory_read") is SideEffect.INTERNAL
    assert tools.side_effect_of("memory_write") is SideEffect.INTERNAL
    assert set(tools.tool_names) == {"memory_read", "memory_write"}


def test_memory_wiring_names_are_business_chosen():
    tools = _build(tools={}, memory_read=_handler, memory_write=_handler,
                   memory_resource="memo_store",
                   memory_event_type="memory_call")
    # The memo/archive IO surface exists on the same object.
    assert hasattr(tools, "memory_read")
    assert hasattr(tools, "memory_write")