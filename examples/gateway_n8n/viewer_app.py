"""Minimal cold-path viewer host over the gateway demo's trace root.

Serves the viewer's trace router (tree / generations / graph / audit /
stats / validate) plus a run listing, pointed at the SAME trace_root
the gateway writes into. Read-only discipline: the viewer never
touches the hot path -- the gateway is the WRITE path, this host is
the READ path (evidence).

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

from ordigovernance.viewer.api.trace import build_trace_router


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

    app = FastAPI(title="ordigovernance gateway demo viewer")
    app.include_router(build_trace_router(
        resolve_reader, resolve_trace_dir=resolve_trace_dir))

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