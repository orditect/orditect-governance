"""RunSession: one run's wiring, plus the ambient run (D2).

The hot path is built once per process and shared by every session:
semaphore queueing is process-global by construction (correct: one
backend, one resource reality). Each session owns its run-scoped
wiring (trace store, budget ledger, orchestrator, action pair) via
build_run_context, exactly as the acceptance grounds do.

Identity discipline (D8): call-plane requests resolve their
(task_id, eid) against the session's hot records; a given task_id
with no record is a 404, a missing task_id mints an ephemeral
identity. Seq slots are allocated per (task_id, purpose) starting
above the agent band, keeping the naming discipline.

Ambient ownership (D2, catch-all): the ambient run attributes
run-less traffic to any EXISTING hot record -- that is the one
documented ownership exemption, and its only surface. Evidence
mixing is bounded to ambient traffic only: ambient carries no
engine semantics, so an attributed call never re-executes another
run's business path, it only lands in the ambient audit trail. User
runs stay strict (descriptor registry or the run root).
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from typing import Any

from ordigovernance.api.naming import SEQ_AGENT_BASE
from ordigovernance.api.tools import check_reserved_payload_keys
from ordigovernance.bridges.direct.context import build_hot_path
from ordigovernance.bridges.direct.llms import build_client_registry
from ordigovernance.runtime.agent.governed_agent import GovernedAgent
from ordigovernance.runtime.lifecycle.run_context import (
    RunContextResources,
    build_run_context,
    teardown_run_context,
)
from ordigovernance.runtime.lifecycle.run_registry import (
    RunsRegistry,
    new_run_id,
)
from ordigovernance.runtime.patterns.dynamic_edge_writer import (
    edge_fact,
    write_edges,
)
from ordigovernance.runtime.tools.governed_tools import GovernedToolSet

from ordigovernance.gateway.memory import DictMemoryBody, RedisMemoryBody
from ordigovernance.gateway.schemas import TaskDescriptor

log = logging.getLogger(__name__)

AMBIENT_RUN_ID = "ambient"

# Attributes child-task registrations to the driving composite (D10).
# Set inside the composite's own asyncio task, so concurrent
# composites stay isolated.
_COMPOSITE_CTX: contextvars.ContextVar[str | None] = \
    contextvars.ContextVar("gateway_composite_id", default=None)

class UnknownVocabularyError(ValueError):
    """The descriptor references unknown impl/tool names (422)."""


class DuplicateTaskError(RuntimeError):
    """The task id is already submitted in this session (409)."""

# Resources every session needs registered in the semaphore table
# (pitfalls 2: one namespace for resources and semaphores).
REQUIRED_SEMAPHORES = ("task_execution", "memo_store")


def _check_semaphore_table(settings, registry) -> None:
    """Fail at boot when a declared resource has no semaphore."""
    missing = set(REQUIRED_SEMAPHORES) - set(settings.semaphores)
    for spec in registry.tools.values():
        if spec.resource not in settings.semaphores:
            missing.add(spec.resource)
    for client_spec in settings.model_clients.values():
        resource = client_spec.get("resource")
        if resource and resource not in settings.semaphores:
            missing.add(resource)
    if missing:
        raise ValueError(
            f"semaphore table is missing resources {sorted(missing)}; "
            f"registered: {sorted(settings.semaphores)} "
            f"(one namespace for resources and semaphores, "
            f"docs/pitfalls.md 2)"
        )


class RunSession:
    """Owns one run: run context, client registry, call identities."""

    def __init__(self, manager: "SessionManager", run_id: str,
                 registry, *, budget_scope: str, budget_max_units: int,
                 register: bool) -> None:
        self.manager = manager
        self.run_id = run_id
        self.root_id = run_id
        self._registry = registry
        self.budget_scope = budget_scope
        self.budget_max_units = budget_max_units
        self._register = register
        self.resources: RunContextResources | None = None
        self.llms: dict = {}
        self.descriptors: dict[str, Any] = {}
        self._seq_counters: dict[tuple[str, str], int] = {}
        self.composites: dict[str, dict] = {}
        self._composite_tasks: dict[str, asyncio.Task] = {}
        self.direct_receipts: dict[str, dict] = {}

    @property
    def hot(self) -> dict:
        return self.manager.hot

    async def open(self) -> None:
        settings = self.manager.settings
        self.resources = await build_run_context(
            self.hot,
            trace_dir=settings.trace_root / self.run_id / "trace",
            budget_scope=self.budget_scope,
            budget_max_units=self.budget_max_units,
            task_factory=self.task_factory,
            make_clients=self._make_clients,
            poll_interval=settings.poll_interval,
        )
        self.llms = self.resources.llms
        # Scope-root discipline (pitfalls 4): the run root must be a
        # real terminal record before any sink-driven reopen passes
        # through it.
        await self.hot["storage"].initialize_task(
            self.root_id, initial_status="succeeded")
        if self._register:
            self.manager.runs.register_run(
                self.run_id, "gateway run", {}, self.budget_scope)
        log.info("run session open: %s (scope %s)", self.run_id,
                 self.budget_scope)

    async def close(self, final_status: str) -> None:
        for task in self._composite_tasks.values():
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
        if self.resources is not None:
            await teardown_run_context(self.resources)
        if self._register:
            balance = None
            budget = getattr(self.resources, "budget", None)
            if budget is not None:
                try:
                    balance = await budget.balance()
                except Exception:
                    pass
            self.manager.runs.finish_run(
                self.run_id, final_status=final_status,
                budget_balance=balance)

    async def task_factory(self, task_id: str):
        """Rebuild one task instance from its descriptor (pitfall 13.8).

        The descriptor registered at submit time is the single
        construction source: recovery paths (HITL resume/retry) get an
        instance identical to the first submission, never a closure
        captured before the run context existed. Unknown ids fail
        loudly, exactly as the reference task factories do.
        """
        return self.assemble_task(task_id)

    def assemble_task(self, task_id: str) -> GovernedAgent:
        """Assemble the task instance for one registered descriptor.

        Public for composite drivers: patterns that submit their own
        children (QualityGatePattern) build instances through this
        single source, exactly like the recovery service does.
        """
        descriptor = self.descriptors.get(task_id)
        if descriptor is None:
            raise KeyError(f"unknown task_id {task_id!r}")
        return self._assemble(descriptor)

    def _surfaces(self) -> dict:
        """The surfaces handed to impl factories.

        Impls read upstream results from the hot record storage and
        declare pins; they never touch orchestration surfaces.
        """
        return {
            "storage": self.hot["storage"],
            "run_id": self.run_id,
            "session": self,
        }

    def _assemble(self, descriptor: TaskDescriptor) -> GovernedAgent:
        """Assemble the GovernedAgent for one descriptor.

        submit_task and task_factory share this single construction
        source (pitfall 13.8). The gateway assembles the governed
        shell itself: per-task tool set, shared llm registry,
        archive backend on the tool set, memo scope on the run's
        budget scope. Engine factories stay uninjected on the open
        tier.
        """
        impl_spec = self._registry.impls.get(descriptor.impl)
        if impl_spec is None:
            raise UnknownVocabularyError(
                f"unknown impl {descriptor.impl!r}; "
                f"registered: {sorted(self._registry.impls)}")
        tool_set = self.build_tool_set(descriptor.task_id,
                                       tool_names=descriptor.tools)
        impl = impl_spec.factory(dict(descriptor.params),
                                 self._surfaces())
        return GovernedAgent(
            impl=impl, task_io=self.hot["storage"], tools=tool_set,
            llms=self.llms,
            archive_backend=tool_set, memo_scope=self.budget_scope,
        )

    async def submit_task(self, descriptor: TaskDescriptor) -> None:
        """Resolve a TaskDescriptor into a submitted GovernedAgent.

        Order (D1, D7): vocabulary check -> duplicate check ->
        assembly -> dependency edges (upstream is evidence/graph,
        never scheduling) -> submit with the run root as the default
        snapshot parent (pitfall 10: drive-layer submissions must
        carry the parent explicitly) -> descriptor registration.

        D7 scope (pitfalls 16.6): the duplicate guard is RUN-scoped
        (the session's descriptor registry), never the shared hot
        path. A record left by a PREVIOUS run under the same task id
        is not a duplicate: the submit gives the new run a fresh
        generation on that record, and the old run's evidence stays
        in its own cold path. Deployments that need cross-run
        uniqueness must mint unique ids in their clients (the n8n
        nodes suffix the execution id).
        """
        self._validate_descriptor(descriptor)
        agent = self._assemble(descriptor)
        parent_id = descriptor.parent_task_id or self.root_id
        if descriptor.upstream:
            # Dependency edges: child depends on parent (evidence D1).
            await write_edges(self.resources.store.dependency, [
                edge_fact(descriptor.task_id, upstream_id,
                          is_primary=True)
                for upstream_id in descriptor.upstream
            ])
        await self.resources.orchestrator.submit(
            agent, task_id=descriptor.task_id,
            parent_task_id=parent_id)
        self.descriptors[descriptor.task_id] = descriptor
        self._track_child(descriptor.task_id)
        log.info("run %s: task %s submitted (impl %s, upstream %s)",
                 self.run_id, descriptor.task_id, descriptor.impl,
                 list(descriptor.upstream))

    def register_descriptor(self, descriptor: TaskDescriptor) -> None:
        """Validate and record a descriptor WITHOUT submitting it.

        Composite drivers register their children's descriptors up
        front so the recovery service can rebuild them on reopen
        (single construction source), while the pattern they wrap
        performs the first submission itself.
        """
        self._validate_descriptor(descriptor)
        self.descriptors[descriptor.task_id] = descriptor
        self._track_child(descriptor.task_id)

    def _validate_descriptor(self, descriptor: TaskDescriptor) -> None:
        impl_spec = self._registry.impls.get(descriptor.impl)
        if impl_spec is None:
            raise UnknownVocabularyError(
                f"unknown impl {descriptor.impl!r}; "
                f"registered: {sorted(self._registry.impls)}")
        for tool_name in (descriptor.tools or []):
            if tool_name not in self._registry.tools:
                raise UnknownVocabularyError(
                    f"unknown tool {tool_name!r} in the tools "
                    f"whitelist; registered: "
                    f"{sorted(self._registry.tools)}")
        # The run root's hot record is initialized at session open;
        # a task under that id would collide with the scope root
        # (pitfalls 4) -- reject it with the same verdict class.
        if descriptor.task_id == self.root_id:
            raise DuplicateTaskError(descriptor.task_id)
        if descriptor.task_id in self.descriptors:
            raise DuplicateTaskError(descriptor.task_id)

    def _track_child(self, task_id: str) -> None:
        """Attribute a child task to the driving composite, if any."""
        composite_id = _COMPOSITE_CTX.get()
        if composite_id is None:
            return
        entry = self.composites.get(composite_id)
        if entry is not None:
            entry["children"].append(task_id)

    # ---- composites (D10) ----------------------------------------------

    def start_composite(self, name: str, params: dict) -> str:
        """Start one registered composite as a background driver task.

        The driver coroutine receives (params, session) and returns
        the outcome dict exposed by the status endpoint. Failures land
        on the composite's own status, never on the run itself.
        """
        spec = self._registry.composites.get(name)
        if spec is None:
            raise UnknownVocabularyError(
                f"unknown composite {name!r}; "
                f"registered: {sorted(self._registry.composites)}")
        composite_id = f"{name}-{uuid.uuid4().hex[:8]}"
        self.composites[composite_id] = {
            "name": name, "status": "running",
            "children": [], "outcome": None,
        }
        self._composite_tasks[composite_id] = asyncio.create_task(
            self._drive_composite(composite_id, spec.factory,
                                  dict(params)))
        log.info("run %s: composite %s started (%s)", self.run_id,
                 composite_id, name)
        return composite_id

    async def _drive_composite(self, composite_id: str, factory,
                               params: dict) -> None:
        """Drive one composite; children attribute via the contextvar."""
        entry = self.composites[composite_id]
        token = _COMPOSITE_CTX.set(composite_id)
        try:
            entry["outcome"] = await factory(params, self)
            entry["status"] = "succeeded"
        except asyncio.CancelledError:
            entry["status"] = "cancelled"
            raise
        except Exception as e:
            entry["status"] = "failed"
            entry["outcome"] = {"error": f"{type(e).__name__}: {e}"}
            log.warning("run %s: composite %s failed: %s", self.run_id,
                        composite_id, e, exc_info=True)
        finally:
            _COMPOSITE_CTX.reset(token)

    async def composite_status(self, composite_id: str) -> dict:
        """Live status of one composite; children read from hot records."""
        entry = self.composites.get(composite_id)
        if entry is None:
            raise KeyError(composite_id)
        children = []
        for task_id in entry["children"]:
            record = await self.hot["storage"].get_task(task_id)
            children.append({
                "task_id": task_id,
                "status": record.get("status"),
                "execution_id": record.get("execution_id"),
            })
        return {
            "composite_id": composite_id,
            "name": entry["name"],
            "status": entry["status"],
            "children": children,
            "outcome": entry["outcome"],
        }

    async def _make_clients(self, hooks: dict) -> dict:
        settings = self.manager.settings
        if settings.make_clients is not None:
            return await settings.make_clients(hooks)
        return build_client_registry(
            settings.base_url or "",
            api_key=settings.api_key or "",
            clients=settings.model_clients,
            governor=self.hot["governor"],
            budget=hooks["budget"],
            store=hooks["store"],
        )

    async def allocate_call_identity(self, task_id: str | None,
                                     purpose: str) -> tuple[str, str, int]:
        """Resolve (task_id, eid, seq) for one call-plane call (D8).

        Ownership discipline (pitfalls 16.7): inside a user run a
        task_id must belong to the run (descriptor registry or the
        run root) before its hot record is read -- the hot path is
        shared across runs, so a record existing is not proof of
        ownership.

        Ambient discipline (D2, catch-all): the ambient session is the
        one documented exemption -- run-less traffic may attribute to
        any EXISTING hot record, and only to an existing one. A
        task_id with no hot record is a 404 there too.
        """
        if task_id is None:
            task_id = f"n8n-call-{uuid.uuid4().hex[:8]}"
            eid = f"e-{uuid.uuid4().hex[:8]}"
        else:
            if (self.run_id != AMBIENT_RUN_ID
                    and task_id != self.root_id
                    and task_id not in self.descriptors):
                raise KeyError(task_id)
            record = await self.hot["storage"].get_task(task_id)
            if not record:
                raise KeyError(task_id)
            eid = record.get("execution_id") or f"e-{uuid.uuid4().hex[:8]}"
        key = (task_id, purpose)
        seq = self._seq_counters.get(key, SEQ_AGENT_BASE) + 1
        self._seq_counters[key] = seq
        return task_id, eid, seq

    def build_tool_set(self, task_id: str,
                       tool_names: list[str] | None = None
                       ) -> GovernedToolSet:
        """Per-call governed tool set stamped with the call's identity.

        Key hygiene (13.10): caller kwargs are the payload and must not
        collide with the governed plumbing (params / call_id / ...); the
        reserved-key check fails loudly here instead of surfacing as a
        confusing TypeError frames away.
        """
        names = (tool_names if tool_names is not None
                 else sorted(self._registry.tools))
        tool_set = GovernedToolSet(
            self.hot["governor"], self.resources.budget,
            self.resources.store, task_id=task_id,
            memory_read_handler=self.manager.memory_body.read,
            memory_write_handler=self.manager.memory_body.write,
            memory_resource="memo_store",
        )
        for name in names:
            spec = self._registry.tools.get(name)
            if spec is None:
                raise KeyError(name)
            tool_set.register(name, spec.factory(self),
                              resource=spec.resource,
                              event_type=spec.event_type,
                              side_effect=spec.side_effect)
        return tool_set


class SessionManager:
    """Hot path owner plus the single-active-run guard (D2, 13.14)."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.hot: dict = {}
        self.memory_body: Any = None
        self.runs = RunsRegistry(settings.trace_root)
        self._registry = None
        self._ambient: RunSession | None = None
        self._active: RunSession | None = None

    @property
    def registry(self):
        """The validated deployment registry (None before startup)."""
        return self._registry

    @property
    def ambient(self) -> RunSession | None:
        return self._ambient

    @property
    def active(self) -> RunSession | None:
        return self._active

    async def startup(self, registry) -> None:
        settings = self.settings
        _check_semaphore_table(settings, registry)
        if settings.redis_url:
            self.hot = await build_hot_path(
                settings.redis_url,
                semaphores=dict(settings.semaphores),
                lease_time=settings.lease_time)
            self.memory_body = RedisMemoryBody(self.hot["redis_client"])
        else:
            try:
                from ordigovernance.testing.hot_path import (
                    build_memory_hot_path,
                )
            except ModuleNotFoundError:
                # The testing fixtures are an optional extra; a bare
                # ModuleNotFoundError would surface frames away from
                # the cause.
                raise RuntimeError(
                    "memory mode (no GATEWAY_REDIS_URL) requires the "
                    "testing fixtures: pip install "
                    "ordigovernance-gateway[memory]"
                ) from None
            self.hot = build_memory_hot_path(dict(settings.semaphores))
            self.memory_body = DictMemoryBody()
        self._registry = registry
        self._ambient = RunSession(
            self, AMBIENT_RUN_ID, registry,
            budget_scope=f"ambient:{uuid.uuid4().hex[:8]}",
            budget_max_units=settings.ambient_budget_max_units,
            register=False)
        await self._ambient.open()

    async def shutdown(self) -> None:
        for session in (self._active, self._ambient):
            if session is not None:
                try:
                    await session.close("shutdown")
                except Exception:
                    log.warning("session %s close failed (ignored)",
                                session.run_id, exc_info=True)
        self._active = None
        self._ambient = None
        client = self.hot.get("redis_client")
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    def resolve(self, run_id: str | None) -> RunSession:
        """Route a call-plane request: no run_id lands in ambient (D2)."""
        if run_id is None or run_id == AMBIENT_RUN_ID:
            if self._ambient is None:
                raise KeyError(AMBIENT_RUN_ID)
            return self._ambient
        if self._active is not None and self._active.run_id == run_id:
            return self._active
        raise KeyError(run_id)

    async def start_user_run(
            self, run_id: str | None = None, *,
            budget_max_units: int | None = None) -> RunSession | None:
        """Open the single active user run; None when one is active."""
        if self._active is not None:
            return None
        run_id = run_id or new_run_id()
        session = RunSession(
            self, run_id, self._registry,
            budget_scope=f"{run_id}:{uuid.uuid4().hex[:8]}",
            budget_max_units=(budget_max_units
                              or self.settings.default_budget_max_units),
            register=True)
        await session.open()
        self._active = session
        return session

    async def finish_user_run(self, run_id: str,
                              final_status: str) -> None:
        """Close the active run after all its tasks settled (D5).

        The guard is released only after a successful close so a
        failed close can be retried by the caller; teardown itself is
        idempotent.
        """
        if self._active is None or self._active.run_id != run_id:
            raise KeyError(run_id)
        await self._active.close(final_status)
        self._active = None