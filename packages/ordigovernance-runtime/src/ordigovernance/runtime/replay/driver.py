"""ReplayDriver: sandboxed reruns over pinned inputs (open mechanics).

The driver owns the replay MECHANICS: replay nodes are submitted under
local ids ("{task_id}-{prefix}"), never touching mainline hot records;
reruns go through the action sink's retry_scope (same reopen path as
HITL), so every generation archives and audits exactly like a mainline
generation. Task building is the caller's callback: the driver
transports pinned_input / upstream_outputs / memo_policy /
side_effect_policy / llm_params opaquely (D5), and the business wiring
assembles the actual task instance.

Observational discipline: the driver never writes dependency edges and
never notifies the dependency governor.

Drift attribution is an engine concern. The driver assembles the
evidence (generation slices, archived results, pin declarations) and
hands it to the injected drift_engine; with no engine wired the
reports carry drift=None. The engine protocol is structural:

    engine.build(local_id, generations, statuses, *,
                 audit_events, results) -> drift report | None
    engine.compose_range(order, edges, node_reports, *,
                         baseline_rows, audit_events,
                         node_pins, pin_task_ids) -> range drift | None

generations may be eid strings or (slice_id, eid) pairs; results maps
eid -> archived result dict. The engine tier ships the real
implementation; the open tier's tests use spies.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from typing import Any, Callable

from ordigovernance.api.memo import MemoBackend
from ordigovernance.api.side_effect import ReusePolicy
from ordigovernance.runtime.archive.archive import load_generation
from ordigovernance.runtime.replay.spec import (
    ReplayInput,
    ReplayRangeReport,
    ReplayReport,
    ReplaySpec,
)
from ordigovernance.runtime.replay.topo_sort import topo_order
from ordigovernance.runtime.task.governed_task import TaskIO

log = logging.getLogger(__name__)

REPLAY_ACTOR = "replay"


def policy_isolated_scope(base_scope: str, *,
                          side_effect_policy: dict | None,
                          llm_params: dict | None) -> str:
    """Derive an isolated memo scope for one experiment's policy.

    The suffix hashes the full experiment declaration (policy table +
    sampling params), so two experiments differing in EITHER land in
    distinct memo domains. Stable: the same declaration always derives
    the same scope, so a rerun of one experiment still hits its own
    previous cache. Consumed by the engine tier's memo layer.
    """
    import hashlib
    import json

    blob = json.dumps(
        {"policy": side_effect_policy or {},
         "llm_params": llm_params or {}},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]
    return f"{base_scope}/exp-{digest}"


class ReplayDriver:
    """Drive single-node and interval replays against one run context."""

    def __init__(
            self,
            orchestrator: Any,
            sink: Any,
            task_io: TaskIO,
            *,
            archive_backend: MemoBackend | None = None,
            audit_reader: Any = None,
            ledger_writer: Any = None,
            drift_engine: Any = None,
            id_prefix: str | None = None,
            step_timeout: float = 300.0,
            receipt_timeout: float = 30.0,
            poll_interval: float = 0.2,
    ) -> None:
        self._orchestrator = orchestrator
        self._sink = sink
        self._task_io = task_io
        self._backend = archive_backend
        # audit_reader exposes .query(task_id=...) over the run's audit
        # stream; when absent, replay reports carry drift=None.
        self._audit_reader = audit_reader
        # ledger_writer receives one experiment-ledger event per
        # replayed node: an object/dict with the experiment declaration.
        self._ledger_writer = ledger_writer
        # drift_engine computes attribution over the assembled evidence;
        # None -> drift=None on every report.
        self._drift = drift_engine
        self._base_prefix = id_prefix or f"replay-{uuid.uuid4().hex[:6]}"
        self._step_timeout = step_timeout
        self._receipt_timeout = receipt_timeout
        self._poll_interval = poll_interval
        # Range-level confidence-upgrade state, filled by
        # _attach_range_node_drift while composing node drifts:
        # archived pin declarations keyed "task/eid", and the mapping
        # local_id -> archived task id for replay generations.
        self._node_pins: dict[str, dict[str, str]] = {}
        self._pin_task_ids: dict[str, str] = {}

    # ---- single node ---------------------------------------------------

    async def replay_node(
            self,
            spec: ReplaySpec,
            build_task: Callable[..., Any],
            *,
            parent_id: str,
    ) -> ReplayReport:
        """Pin one node's input and rerun it spec.times generations."""
        pinned = await spec.input.resolve(
            self._backend, reader_eid=self._base_prefix
        )
        await self._write_ledger(spec, pinned)
        report = await self._drive(
            spec.task_id, build_task,
            pinned_input=pinned,
            memo_policy=spec.memo_policy,
            side_effect_policy=spec.side_effect_policy,
            llm_params=spec.llm_params,
            times=spec.times,
            parent_id=parent_id,
            prefix=self._base_prefix,
        )
        drift = await self._build_drift(report)
        if drift is None:
            return report
        return ReplayReport(
            task_id=report.task_id, local_id=report.local_id,
            generations=report.generations, statuses=report.statuses,
            final_record=report.final_record, drift=drift,
        )

    async def _write_ledger(self, spec: ReplaySpec,
                            resolved_pin: dict) -> None:
        """One experiment-ledger event per replayed node.

        The event records the experiment declaration (label, policy
        table, sampling params, pin provenance) so a replay round is
        traceable to the hypothesis it tested. Failures never abort
        the replay: the ledger is observability, not control flow.
        """
        if self._ledger_writer is None:
            return
        event = {
            "type": "replay_experiment",
            "task_id": spec.task_id,
            "local_id": f"{spec.task_id}-{self._base_prefix}",
            "label": spec.label,
            "times": spec.times,
            "memo_policy": spec.memo_policy,
            "side_effect_policy": spec.side_effect_policy,
            "llm_params": spec.llm_params,
            "input_source": spec.input.source,
            "pin_from": spec.input.pin_from,
            "isolate_policy": spec.isolate_policy,
        }
        try:
            write = getattr(self._ledger_writer, "write", None)
            if write is not None:
                await write(event)
            elif callable(self._ledger_writer):
                await self._ledger_writer(event)
        except Exception:
            log.warning("replay ledger write failed (ignored)",
                        exc_info=True)

    async def _build_drift(self, report: ReplayReport):
        """Assemble the node's drift evidence; None when unavailable."""
        if (self._audit_reader is None or self._drift is None
                or len(report.generations) < 2):
            return None
        try:
            events = await self._audit_reader.query(
                task_id=report.local_id
            )
        except Exception:
            log.warning("drift: audit query failed (drift=None)",
                        exc_info=True)
            return None
        results: dict[str, Any] = {}
        for eid in report.generations:
            if not eid:
                continue
            doc = await load_generation(
                self._backend, task_id=report.local_id, eid=eid,
                reader_task_id="replay-drift",
                reader_eid=self._base_prefix,
            )
            results[eid] = (doc or {}).get("result")
        return self._drift.build(
            report.local_id, report.generations, report.statuses,
            audit_events=events, results=results,
        )

    # ---- interval -------------------------------------------------------

    async def replay_range(
            self,
            start_id: str,
            end_id: str,
            *,
            edges: list[dict],
            input: ReplayInput,
            build_task: Callable[..., Any],
            parent_id: str,
            times: int = 1,
            memo_policy: ReusePolicy | None = None,
            side_effect_policy: dict | None = None,
            llm_params: dict | None = None,
            baselines: dict[str, tuple[str, str]] | None = None,
    ) -> ReplayRangeReport:
        """Rerun the interval [start_id, end_id] (D2: tasks stay dynamic).

        The start node is pinned; every downstream node receives the
        upstream replay outputs via upstream_outputs. Each round runs
        under a fresh id prefix so rounds never collide.
        side_effect_policy / llm_params are transported to build_task
        opaquely and apply to every node in the interval.

        baselines: optional pre-resolved {task_id: (eid, status)}
        baseline generations for the drift comparison. When omitted
        they are read from the hot records (the mainline's LATEST
        generation). Callers whose hot path is shared across runs
        MUST pass baselines derived from the run's OWN evidence (its
        snapshot bundle): the hot records are shared process-wide and
        point at whichever run touched them last, so hot-derived
        baselines silently contaminate this run's drift anchor with
        another run's generations.

        Drift evidence: the audit snapshot is taken AFTER all replay
        rounds ran (a snapshot taken up front would capture only
        mainline traffic and every replay generation would slice to
        zero). Every node's per-round report then carries its
        CUMULATIVE drift -- the mainline baseline generation compared
        against the replay rounds, with baseline audit traffic sliced
        under the mainline task id and replay traffic under each
        round's local id. The range report additionally composes the
        per-node drifts into a topology-level attribution chain via
        the injected drift engine. Degrades to drift=None when the
        audit reader or the engine is absent, and to per-node
        degradation when the baseline evidence is incomplete.
        """
        self._node_pins = {}
        self._pin_task_ids = {}
        order = tuple(topo_order(start_id, end_id, edges))
        pinned = await input.resolve(
            self._backend, reader_eid=self._base_prefix
        )
        if baselines is None:
            baselines = await self._read_baselines(order)
        rounds: list[tuple[ReplayReport, ...]] = []
        for round_index in range(1, times + 1):
            prefix = (self._base_prefix if times == 1
                      else f"{self._base_prefix}-r{round_index}")
            upstream_outputs: dict[str, dict] = {}
            reports: list[ReplayReport] = []
            for task_id in order:
                is_start = task_id == start_id
                report = await self._drive(
                    task_id, build_task,
                    pinned_input=pinned if is_start else None,
                    memo_policy=memo_policy,
                    times=1,
                    parent_id=parent_id,
                    prefix=prefix,
                    upstream_outputs=dict(upstream_outputs),
                    side_effect_policy=side_effect_policy,
                    llm_params=llm_params,
                )
                reports.append(report)
                upstream_outputs[task_id] = (
                        report.final_record.get("result") or {}
                )
            rounds.append(tuple(reports))

        # The audit snapshot is taken AFTER all replay rounds ran:
        # reading it before the rounds would capture only mainline
        # traffic and every replay generation would slice to zero.
        events: list[Any] | None = None
        if self._audit_reader is not None and self._drift is not None:
            try:
                events = await self._audit_reader.query()
            except Exception:
                log.warning(
                    "range drift: audit query failed (drift=None)",
                    exc_info=True)
                events = None
        if events is not None:
            rounds[-1] = await self._attach_range_node_drift(
                order, baselines, rounds, events)
        range_drift = None
        if events is not None:
            use_baseline = bool(order) and all(
                t in baselines for t in order)
            node_reports = {rep.task_id: rep.drift for rep in rounds[-1]}
            range_drift = self._drift.compose_range(
                order, edges, node_reports,
                baseline_rows=1 if use_baseline else 0,
                audit_events=events,
                node_pins=self._node_pins,
                pin_task_ids=self._pin_task_ids,
            )
        return ReplayRangeReport(
            start_id=start_id, end_id=end_id, order=order,
            rounds=tuple(rounds), drift=range_drift,
        )

    async def _read_baselines(self, order: tuple[str, ...]
                              ) -> dict[str, tuple[str, str]]:
        """Latest mainline generation per node: the drift baseline.

        {task_id: (eid, status)} for nodes whose hot record survives.
        Nodes without a record are absent from the map; the caller
        treats the baseline as present only when EVERY node has one,
        keeping per-node generation rows aligned across the interval.
        """
        baselines: dict[str, tuple[str, str]] = {}
        for task_id in order:
            try:
                record = await self._task_io.get_task(task_id)
            except Exception:
                continue
            eid = record.get("execution_id")
            if eid:
                baselines[task_id] = (eid, record.get("status", "unknown"))
        return baselines

    async def _attach_range_node_drift(
            self,
            order: tuple[str, ...],
            baselines: dict[str, tuple[str, str]],
            rounds: list[tuple[ReplayReport, ...]],
            events: list[Any],
    ) -> tuple[ReplayReport, ...]:
        """Rebuild the latest round's reports with cumulative node drift.

        Each node's drift compares the mainline baseline generation
        (when every hot record survives) against every replay round so
        far: baseline audit traffic is sliced under the mainline task
        id, replay traffic under each round's local id. Without a
        complete baseline the report degrades to replay-rounds-only
        evidence (single-generation verdicts at times=1). Also reads
        the replay generations' archived pins for the range-level
        confidence upgrade (pin-declared / content-verified).
        """
        use_baseline = bool(order) and all(t in baselines for t in order)
        latest = rounds[-1]
        out: list[ReplayReport] = []
        for index, task_id in enumerate(order):
            rep = latest[index]
            gen_specs: list[tuple[str, str]] = []
            statuses: list[str] = []
            if use_baseline:
                baseline_eid, baseline_status = baselines[task_id]
                gen_specs.append((task_id, baseline_eid))
                statuses.append(baseline_status)
            for past in rounds:
                past_rep = past[index]
                gen_specs.append(
                    (past_rep.local_id, past_rep.generations[-1]))
                statuses.append(past_rep.statuses[-1])
            results, pins_map = await self._load_results_and_pins(
                gen_specs)
            self._node_pins.update(pins_map)
            self._pin_task_ids[rep.local_id] = task_id
            drift = self._drift.build(
                rep.local_id, tuple(gen_specs), tuple(statuses),
                audit_events=events, results=results)
            out.append(ReplayReport(
                task_id=rep.task_id, local_id=rep.local_id,
                generations=rep.generations, statuses=rep.statuses,
                final_record=rep.final_record, drift=drift))
        return tuple(out)

    async def _load_results_and_pins(
            self, gen_specs: list[tuple[str, str]]
    ) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
        """Archived results and pin declarations for drift generations.

        gen_specs are (archive_task_id, eid) pairs: (mainline task id,
        baseline eid) for the baseline row, (local id, round eid) for
        replay rows -- the slice id doubles as the archive's task id.
        Returns ({eid: result}, {"{task_id}/{eid}": input_pins}).
        """
        results: dict[str, Any] = {}
        pins_map: dict[str, dict[str, str]] = {}
        for archive_task_id, eid in gen_specs:
            if not eid:
                continue
            doc = await load_generation(
                self._backend, task_id=archive_task_id, eid=eid,
                reader_task_id="replay-drift",
                reader_eid=self._base_prefix,
            )
            results[eid] = (doc or {}).get("result")
            pins = (doc or {}).get("input_pins")
            if pins:
                pins_map[f"{archive_task_id}/{eid}"] = dict(pins)
        return results, pins_map

    # ---- mechanics -------------------------------------------------------

    async def _drive(
            self,
            task_id: str,
            build_task: Callable[..., Any],
            *,
            pinned_input: dict | None,
            memo_policy: ReusePolicy | None,
            times: int,
            parent_id: str,
            prefix: str,
            upstream_outputs: dict[str, dict] | None = None,
            side_effect_policy: dict | None = None,
            llm_params: dict | None = None,
    ) -> ReplayReport:
        local_id = f"{task_id}-{prefix}"
        build_kwargs: dict = {
            "pinned_input": pinned_input,
            "memo_policy": memo_policy,
            "upstream_outputs": upstream_outputs or {},
            "local_id": local_id,
        }
        # Replay-channel fields are only forwarded when the build_task
        # callback declares them (backwards compatibility with callbacks
        # that predate the unified policy table).
        sig = inspect.signature(build_task)
        for extra_key, extra_value in (
                ("side_effect_policy", side_effect_policy),
                ("llm_params", llm_params)):
            if extra_key in sig.parameters:
                build_kwargs[extra_key] = extra_value
        task = build_task(task_id, **build_kwargs)
        if inspect.isawaitable(task):
            # build_task may be async (e.g. impls that await inside
            # construction): await it so the submitted object is the
            # task itself, never a pending coroutine.
            task = await task
        await self._orchestrator.submit(
            task, task_id=local_id, parent_task_id=parent_id
        )
        statuses: list[str] = []
        record = await self._orchestrator.wait_terminal(
            local_id, timeout=self._step_timeout
        )
        statuses.append(record["status"])

        for _ in range(times - 1):
            prev_count = len(record.get("previous_execution_ids", []))
            receipt = await self._sink.retry_scope(
                parent_id, {local_id}, actor=REPLAY_ACTOR
            )
            await self._wait_reopened(receipt.action_id, local_id,
                                      prev_count)
            record = await self._orchestrator.wait_terminal(
                local_id, timeout=self._step_timeout
            )
            statuses.append(record["status"])

        generations = tuple(
            list(record.get("previous_execution_ids", []))
            + [record.get("execution_id", "")]
        )
        return ReplayReport(
            task_id=task_id, local_id=local_id,
            generations=generations, statuses=tuple(statuses),
            final_record=record,
        )

    async def _wait_reopened(self, action_id: str, local_id: str,
                             prev_count: int) -> None:
        """Wait until the retry action's receipt exists and the node's
        generation counter has advanced."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._receipt_timeout
        while loop.time() < deadline:
            receipt = await self._sink.get_receipt(action_id)
            if receipt is not None:
                record = await self._task_io.get_task(local_id)
                if len(record.get("previous_execution_ids", [])) \
                        > prev_count:
                    return
            await asyncio.sleep(self._poll_interval)
        raise TimeoutError(f"reopen of {local_id} not confirmed")