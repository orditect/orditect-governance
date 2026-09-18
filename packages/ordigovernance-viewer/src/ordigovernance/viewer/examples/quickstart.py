"""The 30-second ordigovernance quickstart: one governed run plus the viewer.

Zero LLM keys, zero external services beyond the orditect framework's
memory adapter: the world is deterministic (mock tool handlers), the
model is scripted (ScriptedLLMClient), and every call still flows
through the real governed plane — semaphore, budget, audit,
generations, archive and pins all land in a trace bundle you can
inspect in the browser.

    python -m ordigovernance.viewer.examples.quickstart
    # then open the printed URL

The demo intentionally runs one researcher TWICE (a reopen into a
second generation): watch the generations panel show two chips, and
the audit stream charge the same world read twice. That repeated
spend is exactly what the engine tier's memo reuse eliminates — the
upgrade hook, visible in the free tier.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ordigovernance.runtime.lifecycle.run_context import (
    build_run_context,
    teardown_run_context,
)
from ordigovernance.runtime.patterns.fanout import FanOutPattern
from ordigovernance.runtime.task.governed_task import GenerationMeta
from ordigovernance.runtime.tools.governed_tools import GovernedToolSet
from ordigovernance.testing.mock_backend import HandlerBackendAdapter
from ordigovernance.testing.mock_llm import ScriptedLLMClient
from ordigovernance.testing import mock_tools

ROOT_ID = "quickstart-root"
RESEARCHERS = ("ev-battery", "ev-charging")
TOOL_SPECS = {
    "search": {"handler": mock_tools.web_search,
               "resource": "web_search", "event_type": "tool_call",
               "side_effect": "readonly"},
}

# ---- governed scripted LLM: B-class atom over the deterministic client ----


class GovernedScriptedLLM:
    """A ScriptedLLMClient behind the governed call plane.

    Every chat() lands as one governed call: semaphore, budget charge,
    audit event, call_id idempotency — with a deterministic body so the
    demo reproduces exactly.
    """

    def __init__(self, governor, budget, store, *, task_id: str,
                 resource: str = "llm") -> None:
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


# ---- business impls (open-tier: runtime passthrough context) -----------------


class ResearcherImpl:
    """One world read (memo slot, executed for real on the free tier)
    plus one LLM analysis."""

    def __init__(self, topic: str) -> None:
        self._topic = topic

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


class PublishImpl:
    """Consume every researcher's archive, pin the inputs, publish."""

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
        report = await ctx.llm_call(
            "publish", "publish", 1,
            messages=[{"role": "user",
                       "content": "Summarize: " + " | ".join(findings)}],
        )
        result = {
            "report": report["choices"][0]["message"]["content"],
            "input_pins": pins,
        }
        await ctx.archive(result, pins=pins)
        return result


# ---- the run --------------------------------------------------------------------


async def _execute(hot: dict, trace_dir: Path) -> None:
    from ordigovernance.runtime.agent.governed_agent import GovernedAgent
    from ordigovernance.runtime.patterns.dynamic_edge_writer import (
        edge_fact,
        write_edges,
    )

    mock_tools.memory_reset()

    def make_tools(task_id: str) -> GovernedToolSet:
        tool_set = GovernedToolSet(
            hot["governor"], _budget["ledger"], _store["store"],
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
                hot["governor"], _budget["ledger"], _store["store"],
                task_id=task_id, resource="llm_research"),
            "publish": GovernedScriptedLLM(
                hot["governor"], _budget["ledger"], _store["store"],
                task_id=task_id, resource="llm_writing"),
        }
        return GovernedAgent(
            impl=impl, task_io=hot["storage"], tools=tools, llms=llms,
            archive_backend=tools, memo_scope="quickstart",
        )

    async def task_factory(task_id: str):
        if task_id in RESEARCHERS:
            return make_agent(task_id, ResearcherImpl(task_id))
        if task_id == "publish":
            return make_agent(task_id, PublishImpl(hot["storage"]))
        raise KeyError(f"unknown task_id {task_id}")

    resources = await build_run_context(
        hot,
        trace_dir=trace_dir,
        budget_scope="quickstart-root:demo",
        budget_max_units=10000,
        task_factory=task_factory,
    )
    _budget["ledger"] = resources.budget
    _store["store"] = resources.store
    try:
        await write_edges(resources.store.dependency, [
            edge_fact(tid, ROOT_ID, is_primary=True) for tid in
            (*RESEARCHERS, "publish")
        ])
        orch = resources.orchestrator
        for tid in RESEARCHERS:
            await orch.submit(make_agent(tid, ResearcherImpl(tid)),
                              task_id=tid, parent_task_id=ROOT_ID)
        await asyncio.gather(*[
            orch.wait_terminal(tid, timeout=60.0) for tid in RESEARCHERS
        ])

        # The hook beat: reopen researcher-1 into a SECOND generation.
        # The world read really executes again on the free tier —
        # watch the audit stream charge it twice.
        await hot["storage"].reopen_task(RESEARCHERS[0])
        await orch.submit(make_agent(RESEARCHERS[0],
                                     ResearcherImpl(RESEARCHERS[0])),
                          task_id=RESEARCHERS[0])
        await orch.wait_terminal(RESEARCHERS[0], timeout=60.0)

        await orch.submit(make_agent("publish",
                                     PublishImpl(hot["storage"])),
                          task_id="publish", parent_task_id=ROOT_ID)
        record = await orch.wait_terminal("publish", timeout=60.0)
        print(f"publish settled: {record['status']}")
        balance = await resources.budget.balance()
        print(f"budget balance after the run: {balance}")
    finally:
        await teardown_run_context(resources)


def _build_hot_path() -> dict:
    """Memory hot path via the testing fixtures (no redis)."""
    from ordigovernance.testing.hot_path import build_memory_hot_path

    return build_memory_hot_path({
        "task_execution": 8,
        "llm_research": 2,
        "llm_writing": 1,
        "web_search": 2,
        "memo_store": 2,
    })

def _build_viewer_app(trace_dir: Path):
    """Assemble the quickstart viewer app; serving is the caller's job.

    Split out of _serve so tests can drive the HTTP surface through
    TestClient without a live server.
    """
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    from ordigovernance.viewer.api.generation import build_generation_router
    from ordigovernance.viewer.api.trace import build_trace_router
    from ordigovernance.viewer.api.watermark import build_watermark_router

    class _Reader:
        def __init__(self):
            from orditect.adapter.local import LocalFileStore

            self._store = LocalFileStore(trace_dir)

        @property
        def snapshot(self):
            return self._store.snapshot

        @property
        def dependency(self):
            return self._store.dependency

        @property
        def audit(self):
            return self._store.audit

    app = FastAPI(title="ordigovernance quickstart")
    app.include_router(build_trace_router(
        lambda run_id: _Reader(),
        resolve_trace_dir=lambda run_id: trace_dir,
    ))
    # The generation-content router speaks the MemoBackend protocol
    # (keyword call_id, payload_fn, envelope results); the mock memory
    # handlers are plain callables, so they go through the handler
    # adapter instead of being handed over bare (a bare module lacks
    # the keyword surface and every read would TypeError).
    app.include_router(build_generation_router(
        lambda run_id: HandlerBackendAdapter(
            mock_tools.memory_read, mock_tools.memory_write)))
    app.include_router(build_watermark_router(
        get_semaphore_status=lambda: _semaphore_status(),
        get_budget_balance=lambda: _budget_balance(),
    ))

    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    if ui_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(ui_dir)), name="ui")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    return app


async def _serve(trace_dir: Path, port: int) -> None:
    import uvicorn

    app = _build_viewer_app(trace_dir)
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    print(f"\nquickstart viewer: http://127.0.0.1:{port}/\n"
          f"trace bundle: {trace_dir}\n"
          f"(Ctrl+C to stop)\n")
    await server.serve()


async def _semaphore_status() -> list:
    """Live semaphore water levels from the registry (never fabricated)."""
    registry = _hot.get("registry")
    if registry is None:
        return []
    try:
        return await registry.get_all_semaphore_status()
    except Exception:
        return []


async def _budget_balance():
    ledger = _budget.get("ledger")
    if ledger is None:
        return None
    try:
        return await ledger.balance()
    except Exception:
        return None


_INDEX_HTML = """<!doctype html><meta charset=utf-8>
<title>ordigovernance quickstart</title>
<style>body{font-family:monospace;background:#0f1115;color:#d7dce2;
margin:24px}pre{background:#171a21;padding:12px;border-radius:6px;
white-space:pre-wrap}h2{color:#8ab4f8;font-size:14px}</style>
<h1>ordigovernance quickstart — governed run evidence</h1>
<h2>generations</h2><pre id=gens>loading...</pre>
<h2>audit (every governed call, charged)</h2><pre id=audit>...</pre>
<h2>validate</h2><pre id=val>...</pre>
<script>
const ROOT = "quickstart-root";
async function poll(){
  try{
    const g = await (await fetch(`/api/runs/quickstart/generations?root_id=${ROOT}`)).json();
    document.getElementById("gens").textContent =
      g.map(s=>`${s.task_id}  ${s.execution_id.slice(0,12)}  ${s.status}`).join("\\n");
    const a = await (await fetch("/api/runs/quickstart/audit")).json();
    document.getElementById("audit").textContent =
      a.map(e=>`[${e.event_type}] ${e.event_id} `+
        `${(e.payload&&e.payload.usage)?("tokens="+e.payload.usage.total_tokens):""} `+
        `${(e.payload&&e.payload.cost_units)!=null?("cost="+e.payload.cost_units):""}`
      ).join("\\n");
    const v = await (await fetch(`/api/runs/quickstart/validate?root_id=${ROOT}`)).json();
    document.getElementById("val").textContent =
      v.available ? `${v.ok?"PASS":"FAIL"} — ${v.summary}` : v.summary;
  }catch(e){}
}
poll(); setInterval(poll, 2000);
</script>"""


def main() -> None:
    port = 8177
    trace_dir = Path(tempfile.mkdtemp(prefix="ordigovernance-quickstart-"))
    hot = _build_hot_path()
    _hot.update(hot)
    asyncio.run(_execute(hot, trace_dir))
    asyncio.run(_serve(trace_dir, port))


_budget: dict = {}
_store: dict = {}
_hot: dict = {}


if __name__ == "__main__":
    main()