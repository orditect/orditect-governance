"""GovernedToolSet: the A-class atom facade.

Migrated from the reference application's app/tools/governed.py and
generalized: instead of a fixed set of business tools, the facade holds
a registry of named handlers. Each registered tool call is a
first-class governed unit (GovernedCallClient): semaphore, budget,
call_id idempotency, audit event with a business-chosen event type, and
content pointer-ization of the call parameters.

When memory handlers are provided, the facade also satisfies the
MemoBackend protocol, so the same object backs both business tool
traffic and the memo/archive content layer.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from orditect.flow import GovernedCallClient

from ordigovernance.api.memo import MemoBackend
from ordigovernance.runtime.tools.content_pointer import params_content_fn
from ordigovernance.api.tools import CostFn, flat_cost
from ordigovernance.api.tools import ToolHandler
from ordigovernance.api.side_effect import SideEffect, normalize_side_effect

class GovernedToolSet(MemoBackend):
    """Registry of governed tools plus the memo/archive IO surface.

    Parameters
    ----------
    governor, budget, store:
        Call-level governance plane objects (resource governor, budget
        ledger, governance store with .audit/.content surfaces).
    task_id:
        Owning task; stamped on every audit event produced by this set.
    cost_fn:
        Pricing strategy shared by all registered tools (default: flat 1).
    memory_read_handler / memory_write_handler:
        Handlers backing the MemoBackend surface. When absent, memo and
        archive operations raise at first use (ungoverned setups should
        pass tools=None to the memo/archive layer instead).
    memory_resource / memory_event_type:
        Governance wiring for the memory tools; business-chosen strings.
    """

    def __init__(
        self,
        governor: Any,
        budget: Any,
        store: Any,
        *,
        task_id: str,
        cost_fn: CostFn | None = None,
        content_type: str = "application/json",
        memory_read_handler: ToolHandler | None = None,
        memory_write_handler: ToolHandler | None = None,
        memory_resource: str = "llm_research",
        memory_event_type: str = "memory_call",
    ) -> None:
        self._governor = governor
        self._budget = budget
        self._store = store
        self._task_id = task_id
        self._cost_fn = cost_fn or flat_cost(1)
        self._content_type = content_type
        self._clients: dict[str, GovernedCallClient] = {}
        self._side_effects: dict[str, SideEffect] = {}
        self._event_types: dict[str, str] = {}
        if memory_read_handler is not None:
            self.register("memory_read", memory_read_handler,
                          resource=memory_resource,
                          event_type=memory_event_type,
                          side_effect=SideEffect.INTERNAL)
        if memory_write_handler is not None:
            self.register("memory_write", memory_write_handler,
                          resource=memory_resource,
                          event_type=memory_event_type,
                          side_effect=SideEffect.INTERNAL)

    def register(self, name: str, handler: ToolHandler, *,
                 resource: str, event_type: str,
                 side_effect: SideEffect | str = SideEffect.READONLY
                 ) -> None:
        """Register one handler as a governed tool under a business name.

        side_effect declares the tool's effect on the world (readonly /
        external / internal). Replay policy routing keys off this tag:
        external tools are stubbed by default during replays unless the
        replay request explicitly selects sandbox or allow.
        """
        self._clients[name] = GovernedCallClient(
            self._governor,
            resource=resource,
            handler=handler,
            budget=self._budget,
            cost_fn=self._cost_fn,
            audit_writer=self._store.audit,
            content_writer=self._store.content,
            event_type=event_type,
            task_id=self._task_id,
            content_type=self._content_type,
        )
        self._side_effects[name] = normalize_side_effect(side_effect)
        self._event_types[name] = event_type

    def side_effect_of(self, name: str) -> SideEffect:
        """Side-effect tag of one registered tool (default: readonly).

        Unknown names are treated as readonly: an unregistered call
        raises KeyError at invocation time anyway, and failing closed
        on the read side is safer than failing open on the write side.
        """
        return self._side_effects.get(name, SideEffect.READONLY)

    async def record_stub_decision(
            self,
            name: str,
            *,
            call_id: str,
            inputs: dict,
            policy_table: Mapping[str, str] | None = None,
    ) -> None:
        """Direct audit write of one stub routing decision.

        A stubbed call is a zero-cost, zero-semaphore DECISION record:
        it bypasses the governed call plane (no budget/semaphore
        traces, by design) and lands on the audit stream directly, so
        a replay's stubbed external calls are countable and
        attributable from the audit stream alone. The call_id remains
        the idempotency key; the event type reuses the tool's
        registered type (no new vocabulary, T6-neutral).
        """
        append = getattr(getattr(self._store, "audit", None), "append",
                         None)
        if append is None:
            return
        from orditect.protocol import AuditEvent

        await append(AuditEvent(
            event_id=call_id,
            task_id=self._task_id,
            event_type=self._event_types.get(name, "tool_call"),
            payload={
                "stubbed": True,
                "would_have_called": {"tool": name, "inputs": inputs},
                "side_effect_policy": dict(policy_table or {}),
            },
        ))

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._clients)

    async def call(self, name: str, *args: Any, call_id: str,
                   params: dict | None = None,
                   payload_fn: Callable[[Any], dict] | None = None,
                   **kwargs: Any) -> Any:
        """Invoke a registered tool as one governed call.

        Every call pointer-izes its parameters (T5); params defaults to
        the empty dict so the audit event always carries a resolvable
        pointer, matching the reference behavior.

        payload_fn: optional audit-payload injector forwarded to the
        governed call. The framework invokes it on EVERY audit outcome
        (success, error, cancel), so the function must be total.
        """
        try:
            client = self._clients[name]
        except KeyError:
            raise KeyError(
                f"unknown governed tool {name!r}; "
                f"registered: {self.tool_names}"
            ) from None
        if payload_fn is not None:
            kwargs["payload_fn"] = payload_fn
        return await client.call(
            *args,
            call_id=call_id,
            content_fn=params_content_fn(params or {}),
            **kwargs,
        )

    # ---- MemoBackend surface -------------------------------------------

    async def memory_read(self, key: str, *, call_id: str,
                          payload_fn: Callable[[Any], dict] | None = None
                          ) -> dict | None:
        return await self.call("memory_read", key, call_id=call_id,
                               params={"key": key}, payload_fn=payload_fn)

    async def memory_write(self, key: str, value: Any, *,
                           call_id: str,
                           payload_fn: Callable[[Any], dict] | None = None
                           ) -> dict:
        return await self.call("memory_write", key, value, call_id=call_id,
                               params={"key": key, "value": value},
                               payload_fn=payload_fn)