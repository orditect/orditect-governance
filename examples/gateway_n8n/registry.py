"""Reference deployment registry for the n8n gateway demo.

Mirrors the acceptance narrative (examples/acceptance): researcher
world reads, a writer composing drafts from succeeded upstreams, an
LLM reviewer whose parsed SCORE line drives the quality gate, and a
publisher pinning its producer generation. The world stays
deterministic (mock search handler); every model call is a real
governed endpoint call billed by real tokens.

Deployment wiring (the gateway never imports this module statically;
import-boundary discipline):

    GATEWAY_REGISTRY_MODULE=examples.gateway_n8n.registry:build_registry

Production deployments ship their registry inside their own installed
package and may additionally register entry points under the groups
ordigovernance.gateway.tools / .impls / .composites; examples/ is not
an installed distribution, so this demo uses the module channel.
"""

from __future__ import annotations

import re

from ordigovernance.gateway.registry import (
    CompositeSpec,
    GatewayRegistry,
    ImplSpec,
    ToolSpec,
)
from ordigovernance.gateway.schemas import TaskDescriptor
from ordigovernance.testing.mock_tools import web_search

_REVIEW_PROMPT = (
    "Review the following draft for factual coverage, structure and "
    "clarity. Write 2-4 sentences of feedback, then end your reply "
    "with exactly one final line of the form 'SCORE: <0-100>'.\n\n"
    "DRAFT:\n"
)

_SCORE_RE = re.compile(r"SCORE:\s*(\d{1,3})", re.IGNORECASE)


def _parse_score(review_text: str) -> int:
    """Parse the review's SCORE line (last match wins); clamp to 0-100.

    Raises ValueError when absent or malformed: the gate's verdict
    source must never silently default -- a default would fabricate
    convergence and invalidate the narrative's evidence.
    """
    matches = _SCORE_RE.findall(review_text or "")
    if not matches:
        raise ValueError(
            f"review did not terminate with a SCORE line: "
            f"{review_text[-200:]!r}"
        )
    return max(0, min(100, int(matches[-1])))


# ---- business impls (factory signature: (params, surfaces) -> impl) ---------


class ResearcherImpl:
    """One memoized world read plus one LLM analysis; archives itself."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._topic = params.get("topic") or "general"

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
        result = {
            "topic": self._topic,
            "snippets": read.get("count"),
            "analysis": analysis["choices"][0]["message"]["content"],
            "origins": dict(ctx.origins),
        }
        await ctx.archive(result, pins={})
        return result


class WriterImpl:
    """Compose a draft from every succeeded upstream's hot record."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._upstream = list(params.get("upstream", []))
        self._storage = surfaces["storage"]

    async def run(self, ctx) -> dict:
        pins: dict[str, str] = {}
        findings: list[str] = []
        for tid in self._upstream:
            rec = await self._storage.get_task(tid)
            if rec.get("status") == "succeeded":
                pins[tid] = rec.get("execution_id", "")
                findings.append(str((rec.get("result") or {})
                                    .get("analysis", "")))
        if not findings:
            raise RuntimeError("no succeeded upstreams to draft from")
        draft = await ctx.llm_call(
            "writing", "write", 1,
            messages=[{"role": "user",
                       "content": "Draft from: " + " | ".join(findings)}],
        )
        result = {"draft": draft["choices"][0]["message"]["content"],
                  "input_pins": pins}
        await ctx.archive(result, pins=pins)
        return result


class ReviewerImpl:
    """Review the producer's draft; the score parses from the review text."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._producer_id = params.get("producer_id", "writer")
        self._storage = surfaces["storage"]

    async def run(self, ctx) -> dict:
        producer_rec = await self._storage.get_task(self._producer_id)
        draft = (producer_rec.get("result") or {}).get("draft", "")
        if not draft:
            raise RuntimeError(
                f"no draft to review on {self._producer_id}")
        review = await ctx.llm_call(
            "writing", "review", 1,
            messages=[{"role": "user",
                       "content": _REVIEW_PROMPT + draft}],
        )
        content = review["choices"][0]["message"]["content"]
        score = _parse_score(content)
        pins = {self._producer_id: producer_rec.get("execution_id", "")}
        result = {"score": score, "review": content,
                  "input_pins": pins}
        await ctx.archive(result, pins=pins)
        return result


class PublisherImpl:
    """Publish the passed draft; pins the producer generation."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        self._producer_id = params.get("producer_id", "writer")
        self._storage = surfaces["storage"]

    async def run(self, ctx) -> dict:
        producer_rec = await self._storage.get_task(self._producer_id)
        draft = (producer_rec.get("result") or {}).get("draft", "")
        if not draft:
            raise RuntimeError(
                f"no draft to publish on {self._producer_id}")
        report = await ctx.llm_call(
            "publish", "publish", 1,
            messages=[{"role": "user",
                       "content": f"Publish: {draft}"}],
        )
        pins = {self._producer_id: producer_rec.get("execution_id", "")}
        result = {"report": report["choices"][0]["message"]["content"],
                  "input_pins": pins}
        await ctx.archive(result, pins=pins)
        return result


# ---- composites -------------------------------------------------------------


def quality_gate_pair_factory(params: dict, session):
    """Build the quality-gate composite driver (D10).

    params: producer_id / judge_id (task ids, default writer /
    reviewer), producer_impl / judge_impl (registry vocabulary),
    producer_params / judge_params, upstream (evidence parents of the
    producer), threshold (default 80), max_iterations (default 3).
    """
    producer_id = params.get("producer_id", "writer")
    judge_id = params.get("judge_id", "reviewer")
    producer_impl = params.get("producer_impl", "writer")
    judge_impl = params.get("judge_impl", "reviewer")
    upstream = list(params.get("upstream", []))
    threshold = int(params.get("threshold", 80))

    async def _drive() -> dict:
        from ordigovernance.runtime.patterns.dynamic_edge_writer import (
            edge_fact,
            write_edges,
        )
        from ordigovernance.runtime.patterns.quality_gate import (
            QualityGateConfig,
            QualityGatePattern,
        )

        judge_params = dict(params.get("judge_params", {}))
        judge_params.setdefault("producer_id", producer_id)
        session.register_descriptor(TaskDescriptor(
            task_id=producer_id, impl=producer_impl,
            params=dict(params.get("producer_params", {})),
            upstream=upstream))
        session.register_descriptor(TaskDescriptor(
            task_id=judge_id, impl=judge_impl,
            params=judge_params, upstream=[producer_id]))
        # Evidence edges (D1): the gate submits the children itself,
        # so the composite writes the declared structure explicitly.
        await write_edges(session.resources.store.dependency, [
            *[edge_fact(producer_id, parent, is_primary=True)
              for parent in (upstream or [session.root_id])],
            edge_fact(judge_id, producer_id, is_primary=True),
        ])
        gate = QualityGatePattern(
            session.resources.orchestrator, session.resources.sink,
            config=QualityGateConfig(
                max_iterations=int(params.get("max_iterations", 3)),
                step_timeout=300.0, receipt_timeout=30.0),
        )
        outcome = await gate.run(
            root_id=session.root_id,
            producer_id=producer_id, judge_id=judge_id,
            parent_task_id=session.root_id,
            build_producer=lambda: session.assemble_task(producer_id),
            build_judge=lambda: session.assemble_task(judge_id),
            score_of=lambda rec: (rec.get("result") or {}).get("score", 0),
            is_pass=lambda score: score >= threshold,
        )
        return {"passed": outcome.passed,
                "iterations": outcome.iterations,
                "scores": list(outcome.scores),
                "degraded": outcome.degraded}

    return _drive()


# ---- registry assembly --------------------------------------------------------


def build_registry() -> GatewayRegistry:
    """The reference vocabulary for the gateway n8n demo."""
    return GatewayRegistry(
        tools={
            "search": ToolSpec(
                factory=lambda session: web_search,
                resource="web_search", event_type="tool_call",
                side_effect="readonly",
                description="deterministic mock web search"),
        },
        impls={
            "researcher": ImplSpec(
                factory=ResearcherImpl,
                description="one world read plus one LLM analysis"),
            "writer": ImplSpec(
                factory=WriterImpl,
                description="draft from succeeded upstream hot records"),
            "reviewer": ImplSpec(
                factory=ReviewerImpl,
                description="LLM review with a parsed SCORE line"),
            "publisher": ImplSpec(
                factory=PublisherImpl,
                description="publish the passed draft with pins"),
        },
        composites={
            "quality_gate_pair": CompositeSpec(
                factory=quality_gate_pair_factory,
                description="producer/judge quality gate over registry "
                            "impls"),
        },
    )