"""QualityGatePattern: bounded iterate-until-pass over one node pair.

Abstracted from the reference driver's draft<->review loop. The gate is
RUN POLICY, not node logic: nodes stay pure (no loop code inside), the
pattern drives submit on the first iteration and reopen via the action
sink afterwards, and decides pass/degraded from the scripted verdict
source provided by the caller.

Verdict sourcing is a callback so the pattern carries no scoring
vocabulary: the business layer decides what "score" means (scripted
sequence, LLM-parsed value, multi-reviewer aggregation via a client
registry).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class QualityGateConfig:
    max_iterations: int = 2
    step_timeout: float = 300.0
    receipt_timeout: float = 15.0


@dataclass(frozen=True)
class QualityGateOutcome:
    passed: bool
    iterations: int
    verdicts: tuple[Any, ...]
    draft_record: dict
    review_record: dict | None

    @property
    def scores(self) -> tuple[Any, ...]:
        """Legacy alias for verdicts (single-score gates)."""
        return self.verdicts

    @property
    def degraded(self) -> bool:
        """Iteration cap reached with a usable producing artifact."""
        return (not self.passed
                and self.draft_record.get("status") == "succeeded")

class QualityGatePattern:
    """Drive one producing node + one judging node until pass or cap."""

    def __init__(self, orchestrator: Any, sink: Any, *,
                 config: QualityGateConfig | None = None) -> None:
        self._orchestrator = orchestrator
        self._sink = sink
        self._config = config or QualityGateConfig()

    async def _wait_receipt(self, action_id: str) -> None:
        """Wait for one sink action's execution receipt.

        Raises TimeoutError when the receipt never lands: silently
        returning would let the gate block on wait_terminal for a
        generation that was never reopened, misreporting the failure
        as a step timeout one frame away from the cause.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.receipt_timeout
        while loop.time() < deadline:
            receipt = await self._sink.get_receipt(action_id)
            if receipt is not None:
                return
            await asyncio.sleep(0.2)
        raise TimeoutError(
            f"receipt for action {action_id} not confirmed within "
            f"{self._config.receipt_timeout}s"
        )

    async def _submit_or_reopen(self, root_id: str, task_id: str,
                                iteration: int,
                                build_task: Callable[[], Any],
                                parent_task_id: str,
                                actor: str) -> None:
        if iteration == 1:
            await self._orchestrator.submit(
                build_task(), task_id=task_id,
                parent_task_id=parent_task_id,
            )
        else:
            receipt = await self._sink.retry_scope(
                root_id, {task_id}, actor=actor
            )
            await self._wait_receipt(receipt.action_id)

    async def run(
            self,
            *,
            root_id: str,
            producer_id: str,
            judge_id: str,
            parent_task_id: str,
            build_producer: Callable[[], Any],
            build_judge: Callable[[], Any],
            score_of: Callable[[dict], Any],
            is_pass: Callable[[Any], bool],
            actor: str = "quality-gate",
    ) -> QualityGateOutcome:
        """Iterate the pair. Returns after pass, cap, or producer failure.

        score_of(review_record) -> verdict (opaque to the gate: a bare
        score, or a business verdict object carrying per-dimension
        feedback; the gate transports it untouched, so the caller can
        feed it into the producer's next iteration).
        is_pass(verdict) -> whether the gate opens.
        """
        verdicts: list[Any] = []
        draft_record: dict = {}
        review_record: dict | None = None

        for iteration in range(1, self._config.max_iterations + 1):
            await self._submit_or_reopen(
                root_id, producer_id, iteration, build_producer,
                parent_task_id, actor,
            )
            draft_record = await self._orchestrator.wait_terminal(
                producer_id, timeout=self._config.step_timeout
            )
            if draft_record["status"] != "succeeded":
                break

            await self._submit_or_reopen(
                root_id, judge_id, iteration, build_judge,
                parent_task_id, actor,
            )
            review_record = await self._orchestrator.wait_terminal(
                judge_id, timeout=self._config.step_timeout
            )
            verdict = score_of(review_record)
            verdicts.append(verdict)
            if is_pass(verdict):
                return QualityGateOutcome(
                    passed=True, iterations=iteration,
                    verdicts=tuple(verdicts), draft_record=draft_record,
                    review_record=review_record,
                )

        return QualityGateOutcome(
            passed=False, iterations=len(verdicts),
            verdicts=tuple(verdicts), draft_record=draft_record,
            review_record=review_record,
        )