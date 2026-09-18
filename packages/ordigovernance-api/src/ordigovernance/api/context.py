"""Engine plug-in protocols for the runtime AgentContext.

The runtime ships mechanism-direct defaults (passthrough). Engines
with richer semantics (memo reuse across generations, replay policy
routing) implement these protocols and are injected at assembly time;
agent and business code never changes across tiers.

Versioning discipline: these protocols are a published contract.
Released protocol members are only ever ADDED, never renamed,
removed, or re-typed in place. A semantics change ships as a new
protocol (e.g. PolicyResolverV2Protocol), never as a silent edit.
Returned report shapes (DriftEngineProtocol.build / compose_range,
ReconcileReportShape) are opaque to the open tier: engines own their
fields, and engines bump their own report shapes when needed; the
open tier only transports them.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from ordigovernance.api.side_effect import CallClass, SideEffect


@runtime_checkable
class MemoLayerProtocol(Protocol):
    """Structural contract of one generation's memo layer."""

    async def get_or_execute(
        self,
        purpose: str,
        seq: int | str,
        inputs: dict,
        execute: Callable[[], Awaitable[dict]],
        *,
        reuse: str = "always",
        origin_seq: int | str | None = None,
        mode: str | None = None,
    ) -> tuple[dict, str]:
        """Return (result, origin): "executed" | "reused:<call_id>" | "ungoverned".

        seq:      the memo slot identity (an int business slot or a
                  content-addressed string slot from tracked atoms).
        reuse:    the call site's declared reuse policy
                  ("always" | "on_resume" | "never"); "never" is the
                  producer alias and refuses every override.
        origin_seq: the seq of the underlying governed call's call_id
                  when it differs from the memo slot; defaults to seq.
        mode:     the pre-resolved effective mode from the policy
                  resolver; when given it replaces the declared reuse
                  (except for producer call sites).
        """
        ...


@runtime_checkable
class PolicyResolverProtocol(Protocol):
    """Structural contract of one generation's policy resolver."""

    @property
    def active(self) -> bool:
        """Whether any replay routing is in force."""
        ...

    @property
    def table(self) -> dict:
        """The policy table as given (read-only snapshot)."""
        ...

    def resolve(
        self,
        purpose: str,
        category: CallClass,
        *,
        side_effect: SideEffect = SideEffect.READONLY,
        declared: str = "always",
    ) -> str:
        """Resolve the effective mode for one call site.

        Returns "always" | "on_resume" | "never" | "stub". "stub" is
        only ever returned for external-tagged calls; callers must
        short-circuit before the memo layer when it appears.
        """
        ...


@runtime_checkable
class DriftEngineProtocol(Protocol):
    """Structural contract of the drift attribution engine.

    The replay driver assembles the evidence (generation slices,
    archived results, pin declarations) and hands it here; the engine
    returns an engine-owned report shape (anything the caller treats
    opaquely, e.g. a DriftReport / RangeDriftReport) or None when the
    evidence is insufficient. The open tier never interprets report
    fields.
    """

    def build(
        self,
        local_id: str,
        generations: tuple,
        statuses: tuple[str, ...],
        *,
        audit_events: list,
        results: dict[str, Any],
    ) -> Any:
        """Per-node drift over one replayed node's generations.

        generations: eid strings, or (slice_id, eid) pairs overriding
        the audit slice id per generation (range replays slice the
        baseline generation under the mainline task id and replay
        generations under each round's local id).
        results: {eid: archived result dict}; missing entries are
        evidence gaps the engine degrades around, never errors.
        """
        ...

    def compose_range(
        self,
        order: tuple[str, ...],
        edges: list,
        node_reports: dict[str, Any],
        *,
        baseline_rows: int,
        audit_events: list,
        node_pins: dict[str, dict[str, str]],
        pin_task_ids: dict[str, str],
    ) -> Any:
        """Topology-level attribution over one replayed DAG interval.

        order: the interval's execution order; order[0] is the pinned
        start node. node_reports: {task_id: per-node build() report or
        None}. baseline_rows: leading rows in each node report that
        predate the replay (excluded from attribution signals).
        node_pins: archived pin declarations keyed "{task_id}/{eid}";
        pin_task_ids: local_id -> archived task id for replay
        generations. Absent inputs degrade confidence, never error.
        """
        ...


@runtime_checkable
class ReconcileFnProtocol(Protocol):
    """Structural contract of the pin-vs-archive-read reconcile callable.

    Injected into the viewer's trace router; the viewer maps findings
    to DR-PIN-* warnings and never interprets finding fields beyond
    the documented names.
    """

    async def __call__(
        self,
        backend: Any,
        audit_events: list,
        *,
        task_id: str,
        eid: str,
    ) -> ReconcileReportShape:
        """Reconcile one generation's declared pins against its
        memload traffic; see ReconcileReportShape for the result."""
        ...


@runtime_checkable
class PinFindingShape(Protocol):
    """Read-only shape of one reconciliation finding."""

    @property
    def kind(self) -> str:
        """"eid_mismatch" | "undeclared_load" | "unread_pin"."""
        ...

    @property
    def target_task_id(self) -> str:
        ...

    @property
    def declared_eid(self) -> str | None:
        ...

    @property
    def actual_eid(self) -> str | None:
        ...


@runtime_checkable
class ReconcileReportShape(Protocol):
    """Read-only shape of one generation's reconciliation report."""

    @property
    def available(self) -> bool:
        """False when the evidence cannot support a verdict (legacy
        bundles, missing archive); the viewer skips such reports."""
        ...

    @property
    def findings(self) -> tuple:
        """PinFindingShape entries; empty when consistent."""
        ...