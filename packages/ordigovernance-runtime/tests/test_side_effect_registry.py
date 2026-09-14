"""Side-effect tags at tool registration (axis A acceptance)."""

from __future__ import annotations

import pytest

from ordigovernance.api.side_effect import SideEffect


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


def _tool_set():
    from ordigovernance.runtime.tools.governed_tools import GovernedToolSet

    return GovernedToolSet(_Governor(), _Budget(), _Store(), task_id="t")


async def _handler(*args, **kwargs):
    return {"ok": True}


def test_register_defaults_to_readonly():
    tools = _tool_set()
    tools.register("search", _handler, resource="r", event_type="tool_call")
    assert tools.side_effect_of("search") is SideEffect.READONLY


def test_register_accepts_enum_and_string():
    tools = _tool_set()
    tools.register("send_email", _handler, resource="r",
                   event_type="tool_call", side_effect=SideEffect.EXTERNAL)
    tools.register("charge", _handler, resource="r",
                   event_type="tool_call", side_effect="external")
    assert tools.side_effect_of("send_email") is SideEffect.EXTERNAL
    assert tools.side_effect_of("charge") is SideEffect.EXTERNAL


def test_unknown_tool_reads_as_readonly():
    tools = _tool_set()
    assert tools.side_effect_of("nope") is SideEffect.READONLY


def test_invalid_side_effect_raises():
    tools = _tool_set()
    with pytest.raises(ValueError, match="unknown side_effect"):
        tools.register("x", _handler, resource="r", event_type="tool_call",
                       side_effect="bogus")


def test_memory_handlers_register_as_internal():
    from ordigovernance.runtime.tools.governed_tools import GovernedToolSet

    tools = GovernedToolSet(
        _Governor(), _Budget(), _Store(), task_id="t",
        memory_read_handler=_handler, memory_write_handler=_handler,
    )
    assert tools.side_effect_of("memory_read") is SideEffect.INTERNAL
    assert tools.side_effect_of("memory_write") is SideEffect.INTERNAL