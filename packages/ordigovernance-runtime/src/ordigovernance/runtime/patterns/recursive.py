"""RecursiveComposition: sequential parent-composed pipeline steps.

Abstracted from the reference application's lineage root: a node that
submits its steps in-tree (children of itself), waits for each step's
terminal state, and aggregates results. Nesting depth is unbounded —
a step may itself be a composed node.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable


class RecursiveComposition:
    """Submit steps sequentially in-tree and aggregate their records."""

    def __init__(self, orchestrator: Any, *, step_timeout: float = 300.0) -> None:
        self._orchestrator = orchestrator
        self._step_timeout = step_timeout

    async def run(
        self,
        steps: Iterable[tuple[str, Callable[[], Any]]],
        *,
        fail_fast: bool = True,
    ) -> dict[str, dict]:
        """Run (step_id, build_task) pairs sequentially.

        Returns {step_id: terminal_record}. fail_fast raises on the
        first non-succeeded step with that step's record attached.
        """
        records: dict[str, dict] = {}
        for step_id, build_task in steps:
            await self._orchestrator.submit(build_task(), task_id=step_id)
            record = await self._orchestrator.wait_terminal(
                step_id, timeout=self._step_timeout
            )
            records[step_id] = record
            if fail_fast and record["status"] != "succeeded":
                raise RuntimeError(
                    f"composition step {step_id} failed: {record['status']}"
                )
        return records