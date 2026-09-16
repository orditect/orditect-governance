"""Shared gateway test fixtures: in-memory hot path + scripted clients."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.config import GatewaySettings
from ordigovernance.gateway.registry import (
    GatewayRegistry,
    ImplSpec,
    ToolSpec,
)
from ordigovernance.testing.mock_llm import ScriptedLLMClient
import logging


async def _search_handler(query: str) -> dict:
    return {"query": query, "hits": 3}


async def _scripted_clients(hooks: dict) -> dict:
    """D12 seam: scripted clients over the governed call plane."""

    class _GovernedScriptedLLM:
        def __init__(self, governor, budget, store, *, task_id: str,
                     resource: str) -> None:
            from orditect.flow import GovernedCallClient

            self._scripted = ScriptedLLMClient()
            self._client = GovernedCallClient(
                governor,
                resource=resource,
                handler=self._handler,
                budget=budget,
                cost_fn=lambda result: (
                    (result.get("usage") or {}).get("total_tokens", 1)
                    if isinstance(result, dict) else 1),
                audit_writer=store.audit,
                content_writer=store.content,
                event_type="llm_call",
                task_id=task_id,
            )

        async def _handler(self, messages, **kwargs):
            return await self._scripted.chat(messages, call_id="internal",
                                             **kwargs)

        async def chat(self, messages, *, call_id, **kwargs):
            return await self._client.call(
                messages, call_id=call_id, **kwargs)

    return {
        "research": _GovernedScriptedLLM(
            hooks["governor"], hooks["budget"], hooks["store"],
            task_id="gateway-test", resource="llm"),
    }


@pytest.fixture
def settings(tmp_path: Path) -> GatewaySettings:
    return GatewaySettings(
        redis_url=None,
        trace_root=tmp_path / "runs",
        auth_token="test-token",
        make_clients=_scripted_clients,
    )


@pytest.fixture
def client(settings):
    app = build_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": "Bearer test-token"}

@pytest.fixture
def registry_client(settings, echo_registry):
    app = build_app(settings, registry=echo_registry)
    with TestClient(app) as c:
        yield c

# ---- task-plane fixtures ----------------------------------------------------



class _EchoImpl:
    """Test impl: archives its params (the memsave-90 beat)."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._params = params

    async def run(self, ctx) -> dict:
        result = {"marker": self._params.get("marker", "none")}
        await ctx.archive(result, pins={})
        return result


class _SlowImpl:
    """Test impl: settles after a delay (finish-409 beat)."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._delay = float(params.get("delay", 1.0))

    async def run(self, ctx) -> dict:
        import asyncio

        await asyncio.sleep(self._delay)
        result = {"slow": True}
        await ctx.archive(result, pins={})
        return result


@pytest.fixture
def echo_registry() -> GatewayRegistry:
    return GatewayRegistry(
        tools={
            "search": ToolSpec(
                factory=lambda session: _search_handler,
                resource="web_search", event_type="tool_call",
                side_effect="readonly",
                description="deterministic test search"),
        },
        impls={
            "echo": ImplSpec(
                factory=lambda params, surfaces: _EchoImpl(params,
                                                           surfaces),
                description="archives its params"),
            "slow": ImplSpec(
                factory=lambda params, surfaces: _SlowImpl(params,
                                                           surfaces),
                description="settles after a delay"),
        },
    )