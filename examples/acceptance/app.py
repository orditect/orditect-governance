"""Open-tier acceptance workflow: one full governed run, in-memory.

Flow: root -> fan-out (3 researchers) -> quality gate (writer/review,
scripted 62 -> 88) -> HITL pause/resume beat -> one explicit reopen
(second generation for researcher-1) -> publish (archive + pins).

The world is deterministic (mock tool handlers), the model is
scripted (a governed wrapper over ScriptedLLMClient), and every call
flows through the real governed plane: semaphores, budget, audit,
generations, archive and pins all land in a trace bundle. No redis,
no LLM endpoint: the whole acceptance ground runs on the orditect
memory adapter so CI can execute it.

Open-tier behavior this ground locks (and which makes its two-run
golden comparison stable): every logical call really executes in
every generation. The engine tier's memo reuse and policy routing
plug in behind the same assembly surface and are verified in the
engine tier's own suite.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from ordigovernance.runtime.agent.governed_agent import GovernedAgent
from ordigovernance.runtime.lifecycle.run_context import (
    build_run_context,
    teardown_run_context,
)
from ordigovernance.runtime.orchestration.cooperative_cancel import (
    cooperative_delay,
)
from ordigovernance.runtime.patterns.dynamic_edge_writer import (
    edge_fact,
    write_edges,
)
from ordigovernance.runtime.patterns.fanout import FanOutPattern
from ordigovernance.runtime.patterns.quality_gate import (
    QualityGateConfig,
    QualityGatePattern,
)
from ordigovernance.runtime.patterns.scripted_beat import ScriptedBeat
from ordigovernance.runtime.tools.governed_tools import GovernedToolSet
from ordigovernance.testing import mock_tools
from ordigovernance.testing.mock_llm import ScriptedLLMClient

log = logging.getLogger(__name__)

ROOT_ID = "acceptance-root"
WRITER_ID = "writer"
REVIEW_ID = "review"
PUBLISH_ID = "publish"
RESEARCHERS = ("ev-battery", "ev-charging", "ev-supply")
TOOL_SPECS = {
    "search": {"handler": mock_tools.web_search,
               "resource": "web_search", "event_type": "tool_call",
               "side_effect": "readonly"},
}


# ---- governed scripted LLM: a B-class atom over the deterministic client ----


class GovernedScriptedLLM:
    """A ScriptedLLMClient behind the governed call plane.

    Every chat() lands as one governed call: semaphore, budget charge,
    audit event, call_id idempotency — with a deterministic body so
    the acceptance narrative reproduces exactly.
    """

    def __init__(self, governor, budget, store, *, task_id: str,
                 resource: str) -> None:
        from orditect.flow import GovernedCallClient

        self._scripted = ScriptedLLMClient()
        self._client = GovernedCallClient(
            governor,
            resource=resource,
            handler=self._handler,
            budget=budget,
            cost_fn=lambda result: (
                (result.get("usage") or {}).get("total_tokens", 1)
                if isinstance(result, dict) else 1),
            audit_writer=store.audit,
            content_writer=store.content,
            event_type="llm_call",
            task_id=task_id,
        )

    async def _handler(self, messages, **kwargs):
        return await self._scripted.chat(messages, call_id="internal",
                                         **kwargs)

    async def chat(self, messages, *, call_id, **kwargs):
        return await self._client.call(
            messages, call_id=call_id, **kwargs)


# ---- business impls ----------------------------------------------------------


class ResearcherImpl:
    """One world read (executed for real on the open tier), one LLM
    analysis, and a cooperative-cancel delay window for the HITL beat."""

    def __init__(self, topic: str, storage, *, delay: float = 0.2) -> None:
        self._topic = topic
        self._storage = storage
        self._delay = delay

    async def run(self, ctx) -> dict:
        read, _ = await ctx.memoize(
            "search", 1, {"q": self._topic},
            lambda: ctx.tool_call(
                "search", self._topic, purpose="search", seq=1,
                params={"q": self._topic}),
        )
        analysis = await ctx.llm_call(
            "research", "analyze", 2,
            messages=[{"role": "user",
                       "content": f"Analyze {self._topic} "
                                  f"using {read!r}"}],
        )
        # HITL pause surface: external pause flips cancel_requested,
        # the node settles as cancelled, and the resume path reruns it.
        await cooperative_delay(self._storage, ctx.meta.task_id,
                                self._delay, slice_seconds=0.05)
        result = {
            "topic": self._topic,
            "snippets": read.get("count"),
            "analysis": analysis["choices"][0]["message"]["content"],
            "origins": dict(ctx.origins),
        }
        await ctx.archive(result, pins={})
        return result


class WriterImpl:
    """Compose a draft from every succeeded researcher's archive."""

    def __init__(self, storage) -> None:
        self._storage = storage

    async def run(self, ctx) -> dict:
        pins: dict[str, str] = {}
        findings: list[str] = []
        for tid in RESEARCHERS:
            rec = await self._storage.get_task(tid)
            if rec.get("status") == "succeeded":
                pins[tid] = rec.get("execution_id", "")
                findings.append(str((rec.get("result") or {})
                                    .get("analysis", "")))
        if not findings:
            raise RuntimeError("no succeeded researchers to draft from")
        draft = await ctx.llm_call(
            "writing", "write", 1,
            messages=[{"role": "user",
                       "content": "Draft from: " + " | ".join(findings)}],
        )
        result = {"draft": draft["choices"][0]["message"]["content"],
                  "input_pins": pins}
        await ctx.archive(result, pins=pins)
        return result


class ReviewImpl:
    """Score the writer's current draft (scripted sequence, real call)."""

    def __init__(self, storage, beats: ScriptedBeat) -> None:
        self._storage = storage
        self._beats = beats

    async def run(self, ctx) -> dict:
        writer_rec = await self._storage.get_task(WRITER_ID)
        draft = (writer_rec.get("result") or {}).get("draft", "")
        generation = 1 + len(writer_rec.get("previous_execution_ids", []))
        await ctx.llm_call(
            "writing", "review", 1,
            messages=[{"role": "user",
                       "content": f"Review this draft: {draft}"}],
        )
        score = self._beats.score(generation)
        pins = {WRITER_ID: writer_rec.get("execution_id", "")}
        result = {"score": score, "generation": generation,
                  "input_pins": pins}
        await ctx.archive(result, pins=pins)
        return result


class PublishImpl:
    """Publish the passed draft; pins the writer generation."""

    def __init__(self, storage) -> None:
        self._storage = storage

    async def run(self, ctx) -> dict:
        writer_rec = await self._storage.get_task(WRITER_ID)
        draft = (writer_rec.get("result") or {}).get("draft", "")
        if not draft:
            raise RuntimeError("no draft to publish")
        report = await ctx.llm_call(
            "publish", "publish", 1,
            messages=[{"role": "user",
                       "content": f"Publish: {draft}"}],
        )
        pins = {WRITER_ID: writer_rec.get("execution_id", "")}
        result = {
            "report": report["choices"][0]["message"]["content"],
            "input_pins": pins,
        }
        await ctx.archive(result, pins=pins)
        return result


# ---- the run -----------------------------------------------------------------

def _build_hot_path() -> dict:
    """Memory hot path via the testing fixtures."""
    from ordigovernance.testing.hot_path import build_memory_hot_path

    return build_memory_hot_path({
        "task_execution": 8,
        "llm_research": 2,
        "llm_writing": 1,
        "web_search": 2,
        "memo_store": 2,
    })


async def execute_acceptance_run(trace_dir: Path) -> dict:
    """Drive the full workflow; returns the publish terminal record."""
    hot = _build_hot_path()
    holder: dict = {}
    beats = ScriptedBeat(score_sequence=(62, 88), pass_threshold=80)

    def make_tools(task_id: str) -> GovernedToolSet:
        tool_set = GovernedToolSet(
            hot["governor"], holder["budget"], holder["store"],
            task_id=task_id,
            memory_read_handler=mock_tools.memory_read,
            memory_write_handler=mock_tools.memory_write,
            memory_resource="memo_store",
        )
        for name, spec in TOOL_SPECS.items():
            tool_set.register(name, spec["handler"],
                              resource=spec["resource"],
                              event_type=spec["event_type"],
                              side_effect=spec["side_effect"])
        return tool_set

    def make_agent(task_id: str, impl) -> GovernedAgent:
        tools = make_tools(task_id)
        llms = {
            "research": GovernedScriptedLLM(
                hot["governor"], holder["budget"], holder["store"],
                task_id=task_id, resource="llm_research"),
            "writing": GovernedScriptedLLM(
                hot["governor"], holder["budget"], holder["store"],
                task_id=task_id, resource="llm_writing"),
            "publish": GovernedScriptedLLM(
                hot["governor"], holder["budget"], holder["store"],
                task_id=task_id, resource="llm_writing"),
        }
        return GovernedAgent(
            impl=impl, task_io=hot["storage"], tools=tools, llms=llms,
            archive_backend=tools, memo_scope="acceptance",
        )

    async def task_factory(task_id: str):
        if task_id in RESEARCHERS:
            return make_agent(task_id, ResearcherImpl(task_id,
                                                      hot["storage"]))
        if task_id == WRITER_ID:
            return make_agent(task_id, WriterImpl(hot["storage"]))
        if task_id == REVIEW_ID:
            return make_agent(task_id, ReviewImpl(hot["storage"], beats))
        if task_id == PUBLISH_ID:
            return make_agent(task_id, PublishImpl(hot["storage"]))
        raise KeyError(f"unknown task_id {task_id}")

    mock_tools.memory_reset()
    resources = await build_run_context(
        hot,
        trace_dir=trace_dir,
        budget_scope="acceptance-root:run",
        budget_max_units=100000,
        task_factory=task_factory,
    )
    holder["budget"] = resources.budget
    holder["store"] = resources.store
    try:
        await write_edges(resources.store.dependency, [
            edge_fact(tid, ROOT_ID, is_primary=True)
            for tid in (*RESEARCHERS, WRITER_ID, REVIEW_ID, PUBLISH_ID)
        ])
        orch = resources.orchestrator

        # Fan-out.
        fanout = FanOutPattern(orch, hot["storage"],
                               edge_io=resources.store.dependency,
                               step_timeout=60.0)
        result = await fanout.run(
            ROOT_ID, list(RESEARCHERS),
            build_child_id=lambda topic: topic,
            build_child=lambda topic, cid: make_agent(
                cid, ResearcherImpl(cid, hot["storage"])),
        )
        if result.failed or result.cancelled:
            raise RuntimeError(
                f"fan-out failed: {result.failed} {result.cancelled}")

        # Quality gate: writer <-> review, scripted 62 -> 88.
        gate = QualityGatePattern(
            orch, resources.sink,
            config=QualityGateConfig(max_iterations=2,
                                     step_timeout=60.0),
        )
        outcome = await gate.run(
            root_id=ROOT_ID, producer_id=WRITER_ID, judge_id=REVIEW_ID,
            parent_task_id=ROOT_ID,
            build_producer=lambda: make_agent(
                WRITER_ID, WriterImpl(hot["storage"])),
            build_judge=lambda: make_agent(
                REVIEW_ID, ReviewImpl(hot["storage"], beats)),
            score_of=lambda rec: (rec.get("result") or {}).get("score", 0),
            is_pass=lambda score: beats.is_pass(score),
        )
        if not outcome.passed:
            raise RuntimeError("quality gate did not converge")

        # Second-generation beat: reopen researcher-1 via the sink's
        # scope retry (the same reopen path as HITL), so the executor
        # really reruns it instead of being idempotently skipped by a
        # manual reopen+submit (if_not_exists would dedup it away).
        receipt = await resources.sink.retry_scope(
            ROOT_ID, {RESEARCHERS[0]}, actor="acceptance")
        await _wait_receipt(resources, receipt.action_id)
        record = await orch.wait_terminal(RESEARCHERS[0],
                                          timeout=60.0)
        if record["status"] != "succeeded":
            raise RuntimeError(
                f"reopened researcher failed: {record['status']}")
        await hot["storage"].reopen_task(RESEARCHERS[0])
        await orch.submit(
            make_agent(RESEARCHERS[0],
                       ResearcherImpl(RESEARCHERS[0], hot["storage"])),
            task_id=RESEARCHERS[0])
        await orch.wait_terminal(RESEARCHERS[0], timeout=60.0)

        await orch.submit(make_agent(PUBLISH_ID,
                                     PublishImpl(hot["storage"])),
                          task_id=PUBLISH_ID, parent_task_id=ROOT_ID)
        record = await orch.wait_terminal(PUBLISH_ID, timeout=60.0)
        log.info("publish settled: %s", record["status"])
        return record
    finally:
        await teardown_run_context(resources)

async def _wait_receipt(resources, action_id: str,
                        timeout: float = 15.0) -> None:
    """Best-effort wait for an action's execution receipt."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        receipt = await resources.sink.get_receipt(action_id)
        if receipt is not None:
            return
        await asyncio.sleep(0.2)

def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    trace_dir = Path("data/acceptance/trace")
    shutil.rmtree(trace_dir.parent, ignore_errors=True)
    record = asyncio.run(execute_acceptance_run(trace_dir))
    print(f"publish settled: {record['status']}")
    print(f"trace bundle: {trace_dir}")
    return 0 if record["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())