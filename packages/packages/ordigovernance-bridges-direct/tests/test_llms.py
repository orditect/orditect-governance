"""build_client_registry: pure construction over client specs.

No network access: GovernedLLMClient construction only holds its
configuration; the governed plane objects are fakes.
"""

from __future__ import annotations

from ordigovernance.bridges.direct.llms import build_client_registry


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


def test_registry_maps_names_to_clients():
    clients = build_client_registry(
        "http://localhost:11434/v1",
        api_key="test-key",
        clients={
            "writing": {"model": "m-write", "resource": "llm"},
            "research": {"model": "m-research", "resource": "llm_research"},
        },
        governor=_Governor(), budget=_Budget(), store=_Store(),
    )
    # Names are business-chosen; the registry never interprets them.
    assert sorted(clients) == ["research", "writing"]
    assert clients["writing"] is not clients["research"]
    assert type(clients["writing"]) is type(clients["research"])


def test_each_spec_yields_exactly_one_client():
    clients = build_client_registry(
        "http://localhost:1/v1",
        api_key="k",
        clients={
            "a": {"model": "m1", "resource": "r1"},
            "b": {"model": "m2", "resource": "r2"},
            "c": {"model": "m3", "resource": "r3"},
        },
        governor=_Governor(), budget=_Budget(), store=_Store(),
    )
    assert len(clients) == 3