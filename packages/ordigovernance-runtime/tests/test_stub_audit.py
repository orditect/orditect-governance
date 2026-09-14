"""Stub decision audit events: the GovernedToolSet direct-write surface.

Covers only the open-tier surface (record_stub_decision on the tool
registry); the ctx/tracked/nested call-path tests belong to the
engine tier, which owns memoize routing.
"""

from __future__ import annotations

import pytest

from ordigovernance.api.side_effect import SideEffect


class _SpyAudit:
    def __init__(self):
        self.events: list = []

    async def append(self, event):
        self.events.append(event)


class TestGovernedToolSetRecordStub:
    @pytest.mark.asyncio
    async def test_record_stub_decision_writes_audit_event(self):
        from ordigovernance.runtime.tools.governed_tools import (
            GovernedToolSet,
        )

        audit = _SpyAudit()
        tools = GovernedToolSet(
            object(), object(),
            type("S", (), {"audit": audit, "content": None})(),
            task_id="worker",
        )

        async def _handler(*args, **kwargs):
            return {"ok": True}

        tools.register("send_email", _handler, resource="smtp",
                       event_type="tool_call",
                       side_effect=SideEffect.EXTERNAL)
        await tools.record_stub_decision(
            "send_email",
            call_id="send_email-worker-e1-3",
            inputs={"to": "ops@example.com"},
            policy_table={"external": "stub"},
        )

        assert len(audit.events) == 1
        event = audit.events[0]
        assert event.event_id == "send_email-worker-e1-3"
        assert event.task_id == "worker"
        assert event.event_type == "tool_call"
        assert event.payload["stubbed"] is True
        assert event.payload["would_have_called"] == {
            "tool": "send_email", "inputs": {"to": "ops@example.com"}}
        assert event.payload["side_effect_policy"] == {"external": "stub"}

    @pytest.mark.asyncio
    async def test_record_stub_decision_degrades_without_audit_surface(self):
        from ordigovernance.runtime.tools.governed_tools import (
            GovernedToolSet,
        )

        tools = GovernedToolSet(
            object(), object(),
            type("S", (), {"audit": None, "content": None})(),
            task_id="worker",
        )

        async def _handler(*args, **kwargs):
            return {"ok": True}

        tools.register("send_email", _handler, resource="smtp",
                       event_type="tool_call",
                       side_effect=SideEffect.EXTERNAL)
        # No audit append surface: silently no-ops (origins remain the
        # fallback evidence track).
        await tools.record_stub_decision(
            "send_email", call_id="cid", inputs={})