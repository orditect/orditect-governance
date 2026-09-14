"""SingleRunManager: one-active-run lifecycle over a shared hot path.

The hot path (storage/governor/quota/semaphores) is shared process-wide,
so runs serialize: starting while another run is active is rejected.
The manager owns the lifecycle only — execution order, business wiring
and run vocabulary stay with the injected callbacks:

    executor(run_id, intent, params, hooks) -> (final_record, resources, balance)
        The caller's run body. hooks carries:
            on_event(dict)         publish to the run's SSE channel
            on_resources_ready(r)  expose run-scoped resources early
                                     (HITL must work DURING the run)
    registry: RunsRegistry for index entries and trace directories.

SSE vocabulary emitted by the manager itself: run_started, run_state,
run_finished, run_failed. Everything else is the executor's business.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ordigovernance.runtime.lifecycle.event_bus import EventBus, jsonable
from ordigovernance.runtime.lifecycle.run_registry import RunsRegistry, new_run_id


class SingleRunManager:
    """Single-active-run lifecycle plus event fan-out."""

    def __init__(
        self,
        *,
        registry: RunsRegistry,
        startup: Callable[[], Awaitable[dict]],
        executor: Callable[..., Awaitable[tuple[dict, Any, int | None]]],
        on_shutdown: Callable[[], Awaitable[None]] | None = None,
        max_event_queue: int = 1000,
    ) -> None:
        """
        startup(): build the shared hot path; returns the hot dict.
        executor: run body, see module docstring.
        on_shutdown(): extra teardown after the hot path closes.
        """
        self.registry = registry
        self.bus = EventBus(maxsize=max_event_queue)
        self.state: dict = {"status": "idle", "run_id": None,
                            "last_result": None}
        self._startup = startup
        self._executor = executor
        self._on_shutdown = on_shutdown
        self._hot: dict = {}
        self._running = False
        self._run_task: asyncio.Task | None = None
        self._resources: Any = None

    # ---- lifecycle ---------------------------------------------------------

    async def startup(self) -> None:
        self._hot = await self._startup()

    async def shutdown(self) -> None:
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except BaseException:
                pass
        if self._on_shutdown is not None:
            try:
                await self._on_shutdown()
            except Exception:
                pass

    @property
    def hot(self) -> dict:
        return self._hot

    @property
    def active_run_id(self) -> str | None:
        return self.state.get("run_id") if self._running else None

    @property
    def resources(self) -> Any:
        """Run-scoped resources while a run is active (HITL surface)."""
        return self._resources

    async def current_budget_balance(self) -> int | None:
        resources = self._resources
        if resources is None or getattr(resources, "budget", None) is None:
            return None
        try:
            return await resources.budget.balance()
        except Exception:
            return None

    # ---- runs -----------------------------------------------------------------

    async def start_run(self, intent: str, params: dict | None = None,
                        *, run_id: str | None = None,
                        budget_scope: str | None = None) -> str | None:
        """Start one background run; None when another run is active."""
        if self._running:
            return None
        if not intent or not intent.strip():
            raise ValueError("intent must not be empty")
        self._running = True
        run_id = run_id or new_run_id()
        self.registry.register_run(
            run_id, intent.strip(), params or {},
            budget_scope or run_id,
        )
        self._run_task = asyncio.create_task(
            self._execute(run_id, intent.strip(), params or {})
        )
        return run_id

    async def _execute(self, run_id: str, intent: str,
                       params: dict) -> None:
        trace_dir = self.registry.run_trace_dir(run_id)

        async def on_event(event: dict) -> None:
            await self.bus.publish(event)

        def on_resources_ready(resources: Any) -> None:
            self._resources = resources

        hooks = {
            "trace_dir": trace_dir,
            "on_event": on_event,
            "on_resources_ready": on_resources_ready,
            "hot": self._hot,
        }

        self.state = {"status": "running", "run_id": run_id,
                      "last_result": None}
        await self.bus.publish({"type": "run_started", "run_id": run_id,
                                "intent": intent, "params": params})

        final_status = "error"
        balance = None
        try:
            final_record, _resources, balance = await self._executor(
                run_id, intent, params, hooks
            )
            final_status = final_record.get("status", "error")
            result = jsonable({
                "status": final_status,
                "result": final_record.get("result"),
            })
            self.state = {"status": "idle", "run_id": run_id,
                          "last_result": result}
            await self.bus.publish(
                {"type": "run_finished", "run_id": run_id, **result})
        except asyncio.CancelledError:
            final_status = "cancelled"
            await self.bus.publish({"type": "run_failed", "run_id": run_id,
                                    "error": "cancelled"})
            raise
        except Exception as e:
            # Record the terminal balance even on failure (budget-cap
            # halts land here): the registry entry is the frontend's
            # only post-mortem source once resources are torn down.
            if self._resources is not None:
                budget = getattr(self._resources, "budget", None)
                if budget is not None:
                    try:
                        balance = await budget.balance()
                    except Exception:
                        pass
            self.state = {"status": "idle", "run_id": run_id,
                          "last_result": {"status": "error", "error": str(e)}}
            await self.bus.publish({"type": "run_failed", "run_id": run_id,
                                    "error": str(e)})
        finally:
            self.registry.finish_run(run_id, final_status=final_status,
                                     budget_balance=balance)
            # Clear run-scoped resources: after teardown their
            # dispatcher is stopped, and leaving them reachable would
            # send the next run's actions into a dead queue.
            self._resources = None
            self._running = False