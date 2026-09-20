"""Minimal cold-path viewer host over the gateway demo's trace root.

Serves the viewer routers pointed at the SAME trace_root the gateway
writes into, plus a run listing:

  - build_trace_router       tree / generations / graph / audit /
                             stats / validate
  - build_generation_router  per-generation archived result + pins
                             (the lineage-walk read the Evidence node
                             consumes)
  - build_watermark_router   semaphore usage + budget balance (SSE;
                             the Evidence node reads one-shot via its
                             HTTP Request fallback, the browser can
                             stream it)

Read-only discipline: the viewer never touches the hot path -- the
gateway is the WRITE path, this host is the READ path (evidence).

Run:
    GATEWAY_TRACE_ROOT=data/gateway-runs \
        python -m examples.gateway_n8n.viewer_app
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from orditect.adapter.local import LocalFileStore

from ordigovernance.viewer.api.generation import build_generation_router
from ordigovernance.viewer.api.trace import build_trace_router
from ordigovernance.viewer.api.watermark import build_watermark_router


def build_viewer_app(trace_root: Path) -> FastAPI:
    """Assemble the demo viewer over one shared trace root."""
    trace_root = Path(trace_root)

    def resolve_trace_dir(run_id: str) -> Path:
        trace_dir = trace_root / run_id / "trace"
        if not trace_dir.is_dir():
            raise HTTPException(
                status_code=404,
                detail=f"no trace bundle for run {run_id!r}")
        return trace_dir

    def resolve_reader(run_id: str):
        return LocalFileStore(resolve_trace_dir(run_id))

    def resolve_backend(run_id: str):
        """MemoBackend over the gateway's shared memory body.

        The gateway writes memo/archive traffic through
        GatewaySettings-driven handlers (redis hash or process dict).
        This demo host mirrors the same backend so generation-content
        reads resolve the exact keys the gateway's governed calls
        wrote. Deployments with a redis gateway should point this at
        the same redis body.
        """
        from ordigovernance.gateway.memory import RedisMemoryBody

        url = os.environ.get("GATEWAY_REDIS_URL") or os.environ.get(
            "REDIS_URL")
        if not url:
            raise HTTPException(
                status_code=501,
                detail="generation-content reads require a redis-backed "
                       "gateway body (set GATEWAY_REDIS_URL); the "
                       "in-memory body lives inside the gateway process "
                       "and is invisible to this host")
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, decode_responses=True)

        class _Backend:
            def __init__(self, body):
                self._body = body

            async def memory_read(self, key, *, call_id, payload_fn=None):
                return await self._body.read(key)

            async def memory_write(self, key, value, *, call_id,
                                   payload_fn=None):
                return await self._body.write(key, value)

        return _Backend(RedisMemoryBody(client))

    app = FastAPI(title="ordigovernance gateway demo viewer")
    app.include_router(build_trace_router(
        resolve_reader, resolve_trace_dir=resolve_trace_dir))
    app.include_router(build_generation_router(resolve_backend))

    def _semaphore_status():
        return []

    async def _budget_balance():
        return None

    # The watermark reads the gateway's live registry, which lives in
    # the gateway process. This host serves an empty shell so the
    # route shape exists; the real water levels are read from the
    # gateway itself in production deployments (or via the compose
    # stack where both share the process). Kept honest: no fabricated
    # numbers, the stream reports an explicit marker instead.
    async def _unavailable_marker():
        return [{"name": "gateway-side", "usage": "?", "limit": 0,
                 "utilization": "?"}]

    app.include_router(build_watermark_router(
        get_semaphore_status=_unavailable_marker,
        get_budget_balance=_budget_balance,
    ))

    @app.get("/api/runs")
    async def list_runs() -> list[dict]:
        if not trace_root.is_dir():
            return []
        return [
            {"run_id": child.name}
            for child in sorted(trace_root.iterdir(), reverse=True)
            if child.is_dir() and (child / "trace").is_dir()
        ]

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    return app


_INDEX_HTML = """<!doctype html><meta charset=utf-8>
<title>gateway demo viewer</title>
<style>body{font-family:monospace;background:#0f1115;color:#d7dce2;
margin:24px}pre{background:#171a21;padding:12px;border-radius:6px;
white-space:pre-wrap}h2{color:#8ab4f8;font-size:14px}
select,button{background:#22262e;color:#d7dce2;border:1px solid #444;
padding:4px 8px}</style>
<h1>ordigovernance gateway demo - cold-path evidence</h1>
<div>run: <select id=run></select>
<button onclick="location.reload()">refresh runs</button></div>
<h2>generations</h2><pre id=gens>...</pre>
<h2>audit (every governed call, charged)</h2><pre id=audit>...</pre>
<h2>validate</h2><pre id=val>...</pre>
<script>
let current = null;
async function loadRuns(){
  const runs = await (await fetch("/api/runs")).json();
  const sel = document.getElementById("run");
  sel.innerHTML = "";
  runs.forEach(r => {
    const o = document.createElement("option");
    o.value = r.run_id; o.textContent = r.run_id;
    sel.appendChild(o);
  });
  if (runs.length && !current) current = runs[0].run_id;
  if (current) sel.value = current;
  sel.onchange = () => { current = sel.value; };
}
async function poll(){
  await loadRuns();
  if (!current) return;
  try{
    const g = await (await fetch(
      `/api/runs/${current}/generations?root_id=${current}`)).json();
    document.getElementById("gens").textContent = g.map(s =>
      `${s.task_id}  ${String(s.execution_id).slice(0,12)}  ${s.status}`
    ).join("\\n") || "<empty>";
    const a = await (await fetch(`/api/runs/${current}/audit`)).json();
    document.getElementById("audit").textContent = a.map(e =>
      `[${e.event_type}] ${e.event_id} ` +
      `${(e.payload&&e.payload.usage)?("tokens="+e.payload.usage.total_tokens):""} `+
      `${(e.payload&&e.payload.cost_units)!=null?("cost="+e.payload.cost_units):""}`
    ).join("\\n") || "<empty>";
    const v = await (await fetch(
      `/api/runs/${current}/validate?root_id=${current}`)).json();
    document.getElementById("val").textContent =
      v.available ? `${v.ok?"PASS":"FAIL"} - ${v.summary}` : v.summary;
  }catch(e){}
}
poll(); setInterval(poll, 3000);
</script>"""


def main() -> None:
    import uvicorn

    trace_root = Path(os.environ.get("GATEWAY_TRACE_ROOT",
                                     "data/gateway-runs"))
    port = int(os.environ.get("VIEWER_PORT", "8181"))
    uvicorn.run(build_viewer_app(trace_root),
                host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()