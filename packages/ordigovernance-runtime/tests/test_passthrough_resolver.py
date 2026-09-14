"""PassthroughResolver: the mechanism-direct policy resolver.

Locks the open-tier contract: no replay routing in force, every call
site's declared reuse stays in force, producer/internal refuse every
override, and the resolver can never reach the stub mode.
"""

from __future__ import annotations

import pytest

from ordigovernance.api.side_effect import STUB, CallClass, SideEffect
from ordigovernance.runtime.agent.context import PassthroughResolver


class TestInactivity:
    def test_not_active(self):
        assert PassthroughResolver().active is False

    def test_empty_table(self):
        assert PassthroughResolver().table == {}

    def test_structural_protocol_match(self):
        from ordigovernance.api.context import PolicyResolverProtocol

        assert isinstance(PassthroughResolver(), PolicyResolverProtocol)


class TestResolution:
    def test_declared_passthrough(self):
        resolver = PassthroughResolver()
        assert resolver.resolve(
            "search", CallClass.READONLY, declared="always") == "always"
        assert resolver.resolve(
            "search", CallClass.READONLY, declared="on_resume") \
            == "on_resume"
        assert resolver.resolve(
            "search", CallClass.READONLY, declared="never") == "never"

    def test_external_side_effect_still_passthrough(self):
        # Mainline runs: external side effects fire normally without
        # a policy; the tag never changes routing on the open tier.
        resolver = PassthroughResolver()
        assert resolver.resolve(
            "send_email", CallClass.READONLY,
            side_effect=SideEffect.EXTERNAL, declared="always") == "always"

    def test_llm_class_declared_passthrough(self):
        resolver = PassthroughResolver()
        assert resolver.resolve(
            "agent-llm", CallClass.LLM, declared="on_resume") == "on_resume"

    @pytest.mark.parametrize("category", [CallClass.PRODUCER,
                                          CallClass.INTERNAL])
    def test_producer_and_internal_refuse_everything(self, category):
        resolver = PassthroughResolver()
        assert resolver.resolve(
            "analyze", category, declared="always") == "never"

    def test_never_returns_stub(self):
        # The stub mode belongs to replay routing (engine tier); a
        # passthrough resolver must never produce it for any input.
        resolver = PassthroughResolver()
        for category in CallClass:
            for effect in SideEffect:
                for declared in ("always", "on_resume", "never"):
                    assert resolver.resolve(
                        "x", category, side_effect=effect,
                        declared=declared) != STUB