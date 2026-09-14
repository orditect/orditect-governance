"""RunContext: per-run wiring build and teardown (requires orditect).

Ported from the reference application's runtime run context and
generalized: everything created here is run-scoped — the trace store
(own directory), the budget ledger (own quota scope), the orchestrator,
the dependency governor, the action-queue HITL pair, and the recovery
service. Tearing a run down never leaks state into the next run.

Client assembly (writing/publish/research split in the reference app)
is a callback: the component layer never picks client names or models.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

SUCCESS_WORDS = frozenset({"succeeded"})


@dataclass
class RunContextResources:
    """Run-scoped governance wiring."""

    store: Any
    budget: Any
    orchestrator: Any
    governor: Any
    sink: Any
    dispatcher: Any
    recovery: Any
    llms: dict
    # The business task factory (rebuild a task instance from task_id),
    # shared with RecoveryService and reused by direct single-node
    # retry paths (HITL retry bypasses scope semantics to avoid
    # reopening running ancestors).
    task_factory: Any = None
    # Hot-record IO (the shared hot path's storage surface): required
    # by HITL direct-retry and cooperative-cancel readers. Defaults to
    # hot["storage"]; overridable for storage-decorated setups.
    task_io: Any = None


async def build_run_context(
    hot: dict,
    *,
    trace_dir: str | Path,
    budget_scope: str,
    budget_max_units: int,
    task_factory: Callable[[str], Awaitable[Any]],
    make_clients: Callable[[dict], Awaitable[dict]] | None = None,
    store_factory: Callable[[Path], Any] | None = None,
    success_words: frozenset = SUCCESS_WORDS,
    poll_interval: float = 0.2,
    clean_trace: bool = True,
) -> RunContextResources:
    """Assemble one run's full governance wiring.

    hot: {"storage": ..., "governor": ..., "quota": ...}.
    make_clients(hooks) -> dict of governed clients. hooks carries
    {"governor", "budget", "store"} for the caller's client factories.
    """
    from orditect.adapter.local import LocalFileStore
    from orditect.adapter.ui import ActionSinkAdapter, MemoryActionQueue
    from orditect.flow import BudgetLedger, TaskOrchestrator
    from orditect.flow.actions import ActionDispatcher
    from orditect.flow.governance import DependencyGovernor
    from orditect.flow.recovery import RecoveryService
    from orditect.flow.snapshot import ProtocolSnapshotSink

    trace_dir = Path(trace_dir)
    if clean_trace:
        shutil.rmtree(trace_dir.parent, ignore_errors=True)
    store = store_factory(trace_dir) if store_factory else LocalFileStore(trace_dir)

    # Per-run unique budget scope: fresh scope per run means a clean
    # budget by construction, no backend key-format knowledge required.
    budget = BudgetLedger(
        hot["quota"], root_task_id=budget_scope,
        max_units=budget_max_units,
    )
    await budget.open()

    orchestrator = TaskOrchestrator(
        hot["storage"], hot["governor"],
        snapshot_sink=ProtocolSnapshotSink(store.snapshot),
    )
    governor = DependencyGovernor(
        hot["storage"],
        success_words=success_words,
        lifecycle=orchestrator.lifecycle,
        audit_writer=store.audit,
        dep_graph_store=store.dependency,
    )

    llms: dict = {}
    if make_clients is not None:
        llms = await make_clients(
            {"governor": hot["governor"], "budget": budget, "store": store}
        )

    queue = MemoryActionQueue()
    sink = ActionSinkAdapter(queue, audit_writer=store.audit)
    recovery = RecoveryService(
        hot["storage"], store.snapshot, orchestrator.executor,
        reuse_terminal_words=success_words,
        task_factory=task_factory,
    )
    dispatcher = ActionDispatcher(
        queue, orchestrator, recovery, poll_interval=poll_interval
    )
    await dispatcher.start()

    resources = RunContextResources(
        store=store, budget=budget, orchestrator=orchestrator,
        governor=governor, sink=sink, dispatcher=dispatcher,
        recovery=recovery, llms=llms, task_factory=task_factory,
        task_io=hot["storage"],
    )
    return resources

async def teardown_run_context(resources: RunContextResources) -> None:
    """Deterministic shutdown order: dispatcher, then executor drains."""
    try:
        await resources.dispatcher.stop()
    except Exception:
        pass
    try:
        await resources.orchestrator.wait_all_finalized()
    except Exception:
        pass