"""Real-endpoint acceptance narrative: the full workflow over a live model.

Same narrative shape as app.py (fan-out -> quality gate -> HITL
pause/resume -> explicit reopen -> publish), but every LLM call goes
to a real OpenAI-compatible endpoint from .env instead of the
scripted client. This is the functional verification ground for the
governance stack under real model behavior: the evidence chain
(audit, generations, archive, pins) must hold identically when the
model's outputs are no longer deterministic.

What stays deterministic BY DESIGN:
  - the world (mock tool handlers): the pause/resume/reopen beats are
    injected by the driver, and a deterministic world keeps the
    narrative comparable across runs;
  - the HITL beats themselves (timing-injected cancels).

What becomes real:
  - every model call (research analysis, draft, review, publish) is a
    real endpoint call, billed by real token usage;
  - the review score is PARSED from the real review text, so the
    quality gate converges when the model says so -- the iteration
    count is evidence, not script. A malformed score fails loudly
    rather than fabricating convergence.

Usage:
    cp .env.example .env   # OPENAI_BASE_URL / OPENAI_API_KEY
    python -m examples.acceptance.real_app
    python -m examples.acceptance.real_app --model my-model --threshold 85

Exit code 0 when the narrative settles succeeded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from examples.acceptance.app import (
    PUBLISH_ID,
    RESEARCHERS,
    REVIEW_ID,
    ROOT_ID,
    TOOL_SPECS,
    WRITER_ID,
    PublishImpl,
    ResearcherImpl,
    WriterImpl,
    _build_hot_path,
    _cancel_after_delay,
    _wait_new_generation,
    _wait_receipt,
)
from ordigovernance.runtime.agent.governed_agent import GovernedAgent
from ordigovernance.runtime.lifecycle.run_context import (
    build_run_context,
    teardown_run_context,
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
from ordigovernance.runtime.tools.governed_tools import GovernedToolSet
from ordigovernance.testing import mock_tools

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

_REVIEW_PROMPT = (
    "Review the following draft for factual coverage, structure and "
    "clarity. Write 2-4 sentences of feedback, then end your reply "
    "with exactly one final line of the form 'SCORE: <0-100>'.\n\n"
    "DRAFT:\n"
)

_SCORE_RE = re.compile(r"SCORE:\s*(\d{1,3})", re.IGNORECASE)


def _load_env_file() -> None:
    """Load the repo-root .env without requiring python-dotenv.

    Same minimal parser as the bridge probe: KEY=value lines, optional
    `export` prefix, optional quotes; existing process environment
    always wins.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return
    except ImportError:
        pass
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _parse_score(review_text: str) -> int:
    """Parse the review's SCORE line (last match wins); clamp to 0-100.

    Raises ValueError when absent or malformed: the gate's verdict
    source must never silently default -- a default would fabricate
    convergence and invalidate the whole narrative's evidence.
    """
    matches = _SCORE_RE.findall(review_text or "")
    if not matches:
        raise ValueError(
            f"review did not terminate with a SCORE line: "
            f"{review_text[-200:]!r}"
        )
    return max(0, min(100, int(matches[-1])))


class RealReviewImpl:
    """Score the writer's current draft with a real model review.

    Replaces the scripted-beat review of the deterministic ground:
    the model reads the draft, writes feedback, and declares a score;
    the quality gate consumes the parsed integer.
    """

    def __init__(self, storage) -> None:
        self._storage = storage

    async def run(self, ctx) -> dict:
        writer_rec = await self._storage.get_task(WRITER_ID)
        draft = (writer_rec.get("result") or {}).get("draft", "")
        if not draft:
            raise RuntimeError("no draft to review")
        review = await ctx.llm_call(
            "writing", "review", 1,
            messages=[{"role": "user",
                       "content": _REVIEW_PROMPT + draft}],
        )
        content = review["choices"][0]["message"]["content"]
        score = _parse_score(content)
        pins = {WRITER_ID: writer_rec.get("execution_id", "")}
        result = {"score": score, "review": content,
                  "input_pins": pins}
        await ctx.archive(result, pins=pins)
        return result


async def execute_real_acceptance_run(
        trace_dir: Path, *,
        base_url: str,
        api_key: str,
        model: str,
        threshold: int = 80,
        max_iterations: int = 3,
        budget_max_units: int = 100000,
        memo_layer_factory=None,
        resolver_factory=None) -> dict:
    """Drive the full narrative over a real endpoint; returns the
    publish terminal record."""
    from ordigovernance.bridges.direct.llms import build_client_registry

    hot = _build_hot_path()
    holder: dict = {}

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

    def make_llms() -> dict:
        # Real governed clients: every chat() lands as a governed call
        # (semaphore, budget by real tokens, audit, idempotency).
        return build_client_registry(
            base_url,
            api_key=api_key,
            clients={
                "research": {"model": model, "resource": "llm_research"},
                "writing": {"model": model, "resource": "llm_writing"},
                "publish": {"model": model, "resource": "llm_writing"},
            },
            governor=hot["governor"],
            budget=holder["budget"],
            store=holder["store"],
        )

    def make_agent(task_id: str, impl) -> GovernedAgent:
        tools = make_tools(task_id)
        return GovernedAgent(
            impl=impl, task_io=hot["storage"], tools=tools,
            llms=make_llms(),
            archive_backend=tools, memo_scope="acceptance-real",
            memo_layer_factory=memo_layer_factory,
            resolver_factory=resolver_factory,
        )

    async def task_factory(task_id: str):
        if task_id in RESEARCHERS:
            return make_agent(task_id, ResearcherImpl(task_id,
                                                      hot["storage"]))
        if task_id == WRITER_ID:
            return make_agent(task_id, WriterImpl(hot["storage"]))
        if task_id == REVIEW_ID:
            return make_agent(task_id, RealReviewImpl(hot["storage"]))
        if task_id == PUBLISH_ID:
            return make_agent(task_id, PublishImpl(hot["storage"]))
        raise KeyError(f"unknown task_id {task_id}")

    mock_tools.memory_reset()
    resources = await build_run_context(
        hot,
        trace_dir=trace_dir,
        budget_scope="acceptance-real:run",
        budget_max_units=budget_max_units,
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

        # The scope root must be a REAL terminal record before any
        # sink-driven reopen (docs/pitfalls.md 4).
        await hot["storage"].initialize_task(
            ROOT_ID, initial_status="succeeded")

        # HITL beat: pause researcher-2. With a real model the flag
        # lands long before the node reaches its cooperative window
        # (search + analyze take seconds), so the window aborts on
        # entry and the node settles cancelled -- the same contract
        # the deterministic ground locks, under real latency.
        pauser = asyncio.create_task(
            _cancel_after_delay(hot["storage"], RESEARCHERS[1], 0.05))
        fanout = FanOutPattern(orch, hot["storage"],
                               edge_io=resources.store.dependency,
                               step_timeout=300.0)
        result = await fanout.run(
            ROOT_ID, list(RESEARCHERS),
            build_child_id=lambda topic: topic,
            build_child=lambda topic, cid: make_agent(
                cid, ResearcherImpl(cid, hot["storage"])),
            parent_task_id=ROOT_ID,
        )
        await pauser
        if result.failed:
            raise RuntimeError(f"fan-out failed: {result.failed}")
        if result.cancelled != (RESEARCHERS[1],):
            raise RuntimeError(
                f"HITL pause did not land as scripted: "
                f"cancelled={result.cancelled}")

        # Resume the paused node via resume_tree: succeeded nodes are
        # reused, the cancelled one reruns on a new generation.
        r2_gen1 = (await hot["storage"].get_task(RESEARCHERS[1]))[
            "execution_id"]
        receipt = await resources.sink.resume_tree(
            ROOT_ID, actor="hitl-acceptance")
        await _wait_receipt(resources, receipt.action_id)
        resumed = await _wait_new_generation(
            hot["storage"], RESEARCHERS[1], r2_gen1, timeout=300.0)
        if resumed["status"] != "succeeded":
            raise RuntimeError(
                f"resumed researcher failed: {resumed['status']}")

        # Quality gate over REAL reviews: the model decides when the
        # draft passes; iteration count becomes evidence, not script.
        gate = QualityGatePattern(
            orch, resources.sink,
            config=QualityGateConfig(max_iterations=max_iterations,
                                     step_timeout=300.0),
        )
        outcome = await gate.run(
            root_id=ROOT_ID, producer_id=WRITER_ID, judge_id=REVIEW_ID,
            parent_task_id=ROOT_ID,
            build_producer=lambda: make_agent(
                WRITER_ID, WriterImpl(hot["storage"])),
            build_judge=lambda: make_agent(
                REVIEW_ID, RealReviewImpl(hot["storage"])),
            score_of=lambda rec: (rec.get("result") or {}).get("score", 0),
            is_pass=lambda score: score >= threshold,
        )
        log.info("quality gate: passed=%s iterations=%s scores=%s",
                 outcome.passed, outcome.iterations, outcome.scores)
        if not outcome.passed:
            raise RuntimeError(
                f"quality gate did not converge within "
                f"{max_iterations} iterations (scores={outcome.scores}, "
                f"threshold={threshold})")

        # Second-generation beat: direct reopen + resubmit of researcher-1.
        r1_gen1 = (await hot["storage"].get_task(RESEARCHERS[0]))[
            "execution_id"]
        await hot["storage"].reopen_task(RESEARCHERS[0])
        await orch.submit(
            make_agent(RESEARCHERS[0],
                       ResearcherImpl(RESEARCHERS[0], hot["storage"])),
            task_id=RESEARCHERS[0])
        reopened = await _wait_new_generation(
            hot["storage"], RESEARCHERS[0], r1_gen1, timeout=300.0)
        if reopened["status"] != "succeeded":
            raise RuntimeError(
                f"reopened researcher failed: {reopened['status']}")

        await orch.submit(make_agent(PUBLISH_ID,
                                     PublishImpl(hot["storage"])),
                          task_id=PUBLISH_ID, parent_task_id=ROOT_ID)
        record = await orch.wait_terminal(PUBLISH_ID, timeout=300.0)
        log.info("publish settled: %s", record["status"])
        return record
    finally:
        await teardown_run_context(resources)


def _print_evidence_summary(trace_dir: Path) -> None:
    """Print the audit evidence chain: per-event usage/cost plus totals."""
    path = trace_dir / "audit.ndjson"
    if not path.is_file():
        print("(no audit bundle)")
        return
    total_tokens = 0
    total_cost = 0
    count = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = event.get("data", event)
        payload = event.get("payload") or {}
        usage = payload.get("usage") or {}
        tokens = usage.get("total_tokens") or 0
        cost = payload.get("cost_units") or 0
        total_tokens += tokens
        total_cost += cost
        count += 1
        print(f"  [{event.get('event_type')}] {event.get('event_id')}"
              + (f" tokens={tokens}" if tokens else "")
              + (f" cost={cost}" if cost else ""))
    print(f"  -- {count} events, {total_tokens} tokens, "
          f"{total_cost} cost units")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    _load_env_file()  # load .env BEFORE reading any defaults from env
    parser = argparse.ArgumentParser(
        description="Real-endpoint acceptance narrative")
    parser.add_argument("--model",
                        default=os.environ.get("PROBE_MODEL",
                                               "gpt-4o-mini"))
    parser.add_argument("--threshold", type=int, default=80,
                        help="quality-gate pass score (0-100)")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--budget", type=int, default=100000,
                        help="budget cap in token units")
    parser.add_argument("--trace-dir",
                        default="data/acceptance-real/trace")
    args = parser.parse_args()

    base_url = (os.environ.get("OPENAI_BASE_URL")
                or os.environ.get("OPENAI_API_BASE"))
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url or not api_key:
        print("FAIL  missing OPENAI_BASE_URL / OPENAI_API_KEY "
              "(set them in .env or export them)")
        return 2

    trace_dir = Path(args.trace_dir)
    shutil.rmtree(trace_dir.parent, ignore_errors=True)
    record = asyncio.run(execute_real_acceptance_run(
        trace_dir,
        base_url=base_url, api_key=api_key, model=args.model,
        threshold=args.threshold, max_iterations=args.max_iterations,
        budget_max_units=args.budget,
    ))
    print(f"publish settled: {record['status']}")
    print(f"trace bundle: {trace_dir}")
    _print_evidence_summary(trace_dir)
    return 0 if record["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())