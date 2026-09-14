"""Stage-aware stream aggregation.

Abstracted from the reference application's publish node: a multi-stage
StreamRunner pipeline whose envelopes are forwarded to a consumer
callback (SSE fan-out, printing, rendering) while content deltas are
aggregated per stage into the task result.

The runner's aggregated text per stage is returned so the snapshot /
result / recovery chain stays intact: streaming is additive, never a
replacement for governance.

Cancellation is cooperative: when task_io + task_id are given, every
envelope boundary re-checks the hot record for a cancel request.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable

from ordigovernance.runtime.orchestration.cooperative_cancel import (
    raise_if_cancelled,
)
from ordigovernance.runtime.task.governed_task import TaskIO


def _event_value(event_type: Any) -> str:
    return getattr(event_type, "value", str(event_type))


async def run_stage_pipeline(
    *,
    events: AsyncIterator[tuple[Any, Any]],
    stage_names: tuple[str, ...] | list[str],
    on_event: Callable[[Any, Any], Awaitable[None]] | None = None,
    task_io: TaskIO | None = None,
    task_id: str | None = None,
) -> dict[str, str]:
    """Consume (envelope, event_type) pairs; aggregate content per stage.

    The current stage is tagged onto the envelope payload for consumers
    (setdefault: the runner's own stage field wins when present).

    Failure discipline: a ``stream.error`` event is forwarded to
    ``on_event`` first (observability on the SSE path) and then raised
    as a ``RuntimeError`` -- a failed executor must never let a
    truncated stream be archived as a success.

    Aggregation discipline: a ``stage.end`` event carrying the runner's
    own aggregated content (``data["result"]["content"]``) wins over
    delta-by-delta accumulation, keeping this consumer consistent with
    any middle stages the runner pipeline may add; envelopes without it
    fall back to delta aggregation.

    Returns {stage_name: aggregated_text}.
    """
    names = list(stage_names)
    parts: dict[str, list[str]] = {name: [] for name in names}
    stage_idx = 0
    async for envelope, event_type in events:
        if task_io is not None and task_id is not None:
            await raise_if_cancelled(task_io, task_id)
        stage = names[min(stage_idx, len(names) - 1)]
        data = getattr(envelope, "data", None)
        if on_event is not None:
            if isinstance(data, dict):
                data.setdefault("stage", stage)
            await on_event(envelope, event_type)
        et = _event_value(event_type)
        if et == "stream.error":
            # A failed executor surfaces as a machine-readable error
            # event (UPSTREAM_INTERRUPTED / INTERNAL). Fail the
            # consuming task loudly instead of letting a partial body
            # be archived as a success.
            code = data.get("code", "?") if isinstance(data, dict) else "?"
            message = (data.get("message", "")
                       if isinstance(data, dict) else "")
            raise RuntimeError(
                f"stream stage {stage!r} failed: [{code}] {message}"
            )
        if et == "stage.end":
            # The runner-aggregated content is authoritative when the
            # envelope carries it; delta aggregation below remains the
            # fallback for envelopes that do not.
            result = data.get("result") if isinstance(data, dict) else None
            content = (result or {}).get("content")
            if isinstance(content, str):
                parts[stage] = [content]
            stage_idx += 1
        elif (et == "stream.delta" and isinstance(data, dict)
              and data.get("kind") == "content"):
            parts[stage].append(data.get("text", ""))
    return {name: "".join(chunks) for name, chunks in parts.items()}