"""One governed run, end to end. Business wiring arrives as callbacks."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from ordigovernance.runtime.lifecycle.run_context import (
    build_run_context,
    teardown_run_context,
)


async def run(
    hot: dict,
    *,
    trace_dir,
    root_id: str,
    budget_max_units: int,
    task_factory: Callable[[str], Awaitable[Any]],
    drive: Callable[[Any], Awaitable[dict]],
    make_clients: Callable[[dict], Awaitable[dict]] | None = None,
    budget_scope: str | None = None,
    poll_interval: float = 0.2,
) -> dict:
    """Assemble the run context, delegate orchestration, tear down.

    drive(resources) owns the business orchestration order (patterns
    composition); it receives the RunContextResources and returns the
    terminal record that closes the run.
    """
    resources = await build_run_context(
        hot,
        trace_dir=trace_dir,
        budget_scope=budget_scope or f"{root_id}:{uuid.uuid4().hex[:8]}",
        budget_max_units=budget_max_units,
        task_factory=task_factory,
        make_clients=make_clients,
        poll_interval=poll_interval,
    )
    try:
        return await drive(resources)
    finally:
        await teardown_run_context(resources)