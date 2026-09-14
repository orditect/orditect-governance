"""GovernedAgent: assembly over business agent implementations.

The component layer deliberately has NO standard agent shape. It is an
assembler: given the business implementation (AgentProtocol), the
A-class tool facade, the B-class client registry, and the governance
wiring, it produces a governed executable whose execute() delegates the
whole composition to the implementation.

Executor contract: GovernedAgent IS a BaseBackEndTask (storage,
governor, execute(task_id, **kwargs), resource_type). The component
layer adds generation/archive/pinned plumbing on top.

Replay surface (D1/D2): pinned_input replaces INPUT acquisition only.
side_effect_policy and memo_policy are transported opaquely to the
injected resolver factory — the runtime itself never interprets them;
routing semantics belong to the engine tier's resolver.

Engine plug-in: the runtime default builds contexts with no memo layer
and passthrough policy routing. Engines with richer semantics are
injected via memo_layer_factory / resolver_factory at assembly time.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from orditect.flow import BaseBackEndTask

from ordigovernance.api.memo import MemoBackend
from ordigovernance.runtime.agent.context import AgentContext
from ordigovernance.runtime.task.governed_task import TaskIO
from ordigovernance.runtime.tools.governed_tools import GovernedToolSet
@runtime_checkable
class AgentProtocol(Protocol):
    """Business agent implementation contract.

    run(ctx) composes A/B atoms freely and returns the result dict that
    will be archived as this generation's output. Pinned input is read
    from self (the impl holds its own pinned payload) — the component
    layer never inspects it.
    """

    async def run(self, ctx: AgentContext) -> dict: ...


class GovernedAgent(BaseBackEndTask):
    """Governed executable wrapping an AgentProtocol implementation."""

    def __init__(
            self,
            impl: AgentProtocol,
            *,
            task_io: TaskIO,
            tools: GovernedToolSet | None = None,
            llms: dict[str, Any] | None = None,
            archive_backend: MemoBackend | None = None,
            memo_scope: str = "global",
            memo_policy: str | None = None,
            pinned_input: dict | None = None,
            resource_type: str = "task_execution",
            governor: Any = None,
            side_effect_policy: dict | None = None,
            llm_params: dict | None = None,
            memo_layer_factory: Any = None,
            resolver_factory: Any = None,
    ) -> None:
        super().__init__(task_io, governor)
        self._task_io = task_io
        self._archive_backend = archive_backend
        self._pinned_input = pinned_input
        self._impl = impl
        self._tools = tools
        self._llms = llms or {}
        self._memo_scope = memo_scope
        self._memo_policy = memo_policy
        self._side_effect_policy = side_effect_policy
        self.resource_type = resource_type
        self._executor_governor: Any = None
        self._llm_params = llm_params
        self._memo_layer_factory = memo_layer_factory
        self._resolver_factory = resolver_factory

    @property
    def pinned_input(self) -> dict | None:
        return self._pinned_input

    @property
    def is_pinned(self) -> bool:
        return self._pinned_input is not None

    @property
    def archive_backend(self) -> MemoBackend | None:
        return self._archive_backend

    async def generation_meta(self, task_id: str):
        from ordigovernance.runtime.task.governed_task import GenerationMeta

        record = await self._task_io.get_task(task_id)
        return GenerationMeta(
            task_id=task_id,
            eid=record.get("execution_id", ""),
            previous_eids=tuple(record.get("previous_execution_ids", [])),
            previous_status=record.get("previous_status"),
        )

    @property
    def impl(self) -> AgentProtocol:
        return self._impl

    def build_context(self, meta) -> AgentContext:
        """Assemble the per-generation context (eid/scope stay inside)."""
        memo_layer = (self._memo_layer_factory(
            self._archive_backend, task_id=meta.task_id, eid=meta.eid,
            previous_status=meta.previous_status, scope=self._memo_scope)
            if self._memo_layer_factory else None)
        resolver = (self._resolver_factory(
            self._side_effect_policy, override=self._memo_policy)
            if self._resolver_factory else None)
        return AgentContext(
            meta,
            tools=self._tools,
            llms=self._llms,
            archive_backend=self._archive_backend,
            memo_scope=self._memo_scope,
            llm_params=self._llm_params,
            memo_layer=memo_layer,
            policy_resolver=resolver,
        )

    @property
    def active_governor(self) -> Any:
        """The governor in force: executor-injected wins, fallback otherwise."""
        return self._executor_governor or self.governor

    async def execute(self, task_id: str, **kwargs: Any) -> dict:
        # The executor injects its governor per execution (orditect
        # dual-layer governance: task boundary here, call points inside
        # the governed clients). Honour it when present; an absent kwarg
        # must never clear a previously injected governor. The
        # assembly-time governor remains the fallback for executor-free
        # runs.
        if "governor" in kwargs:
            self._executor_governor = kwargs["governor"]
        meta = await self.generation_meta(task_id)
        ctx = self.build_context(meta)
        return await self._impl.run(ctx)


def assemble_agent(
    impl: AgentProtocol,
    *,
    task_io: TaskIO,
    tools: GovernedToolSet | None = None,
    llms: dict[str, Any] | None = None,
    archive_backend: MemoBackend | None = None,
    memo_scope: str = "global",
    memo_policy: str | None = None,
    pinned_input: dict | None = None,
    resource_type: str = "task_execution",
    governor: Any = None,
    side_effect_policy: dict | None = None,
    llm_params: dict | None = None,
    memo_layer_factory: Any = None,
    resolver_factory: Any = None,
) -> GovernedAgent:
    """One-call assembly: business facts in, governed agent out."""
    return GovernedAgent(
        impl=impl,
        task_io=task_io,
        tools=tools,
        llms=llms,
        archive_backend=archive_backend,
        memo_scope=memo_scope,
        memo_policy=memo_policy,
        pinned_input=pinned_input,
        resource_type=resource_type,
        governor=governor,
        side_effect_policy=side_effect_policy,
        llm_params=llm_params,
        memo_layer_factory=memo_layer_factory,
        resolver_factory=resolver_factory,
    )