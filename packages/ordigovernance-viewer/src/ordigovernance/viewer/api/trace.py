"""Cold-path trace endpoints, parameterized by run_id.

Every read goes through the reader_factory pointed at that run's
private trace directory — the hot path is never touched, so polling is
free of side effects on any running workflow.

resolve_reader(run_id) must 404 unknown runs (the caller owns run-id
validation policy, including the active-run special case).

Pin reconciliation is opt-in: pass reconcile_fn, a callable matching
ordigovernance.api.ReconcileFnProtocol (takes (backend, lines, *,
task_id, eid), returns a ReconcileReportShape), to compare declared
pins against actual archive-read traffic and surface DR-PIN-*
warnings. Without it the validate endpoint returns run_rules findings
only.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException

from ordigovernance.api.context import ReconcileFnProtocol
from ordigovernance.runtime.lifecycle.event_bus import jsonable


def build_trace_router(
    resolve_reader,
    *,
    resolve_trace_dir=None,
    resolve_archive_backend=None,
    reconcile_fn: ReconcileFnProtocol | None = None,
    prefix: str = "/api/runs/{run_id}",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the trace router.

    resolve_reader(run_id) -> reader with .snapshot/.dependency/.audit
    surfaces. resolve_trace_dir(run_id) -> Path of the trace bundle
    (required only by /validate; when absent /validate returns 501).
    resolve_archive_backend(run_id) -> MemoBackend for the reconcile
    channel; reconcile_fn does the actual comparison (injected so the
    viewer carries no engine vocabulary). Both resolvers MUST raise
    HTTPException(404) for unknown run ids.
    """
    router = APIRouter(prefix=prefix, tags=tags or ["trace"])

    @router.get("/tree")
    async def get_tree(run_id: str, root_id: str):
        """Latest-generation lineage tree (state: where the run is)."""
        snaps = await resolve_reader(run_id).snapshot.get_tree(
            root_id, latest_only=True
        )
        return [jsonable(s) for s in snaps]

    @router.get("/generations")
    async def get_generations(run_id: str, root_id: str):
        """Time-travel view: every execution generation of every node."""
        snaps = await resolve_reader(run_id).snapshot.get_tree(
            root_id, latest_only=False
        )
        return [jsonable(s) for s in snaps]

    @router.get("/graph")
    async def get_graph(run_id: str, root_id: str):
        """Dependency graph (structure: who depends on whom)."""
        graph = await resolve_reader(run_id).dependency.read_graph(root_id)
        payload = jsonable(graph)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("task_ids", [])
        payload.setdefault("edges", [])
        return payload

    @router.get("/audit")
    async def get_audit(run_id: str, task_id: str | None = None):
        """Audit events with usage, elapsed, cost."""
        rows = await resolve_reader(run_id).audit.query(task_id=task_id)
        return [jsonable(e) for e in rows]

    @router.get("/stats")
    async def get_stats(run_id: str):
        """Aggregate node counts grouped by status."""
        return await resolve_reader(run_id).snapshot.aggregate(
            group_by="status"
        )

    @router.get("/validate")
    async def validate_bundle(run_id: str,
                              root_id: str | None = None):
        """Self-certify this run's trace bundle against the data rules."""
        if resolve_trace_dir is None:
            raise HTTPException(
                status_code=501,
                detail="validate requires resolve_trace_dir",
            )
        from orditect.protocol.rules import run_rules

        trace_dir = resolve_trace_dir(run_id)
        lines: list[dict] = []
        for name in ("snapshots.ndjson", "audit.ndjson", "deps.ndjson"):
            path = trace_dir / name
            if not path.is_file():
                continue
            for x in path.read_text().splitlines():
                if not x.strip():
                    continue
                try:
                    lines.append(json.loads(x))
                except json.JSONDecodeError:
                    # A poll can race the executor's ndjson append and
                    # see a partially-written tail line; skip it rather
                    # than failing the whole validation read.
                    continue
        if not lines:
            return {"available": False,
                    "summary": "no trace bundle yet", "ok": None}
        report = run_rules(lines)
        findings = []
        for f in getattr(report, "findings", []):
            sev = getattr(f, "severity", None)
            sev = getattr(sev, "value", sev)
            findings.append({
                "severity": sev,
                "rule": (getattr(f, "rule_id", None)
                         or getattr(f, "rule", None)),
                "location": getattr(f, "location", None),
                "message": getattr(f, "message", None),
            })
        # Pin-vs-memload reconciliation (opt-in, injected): for every
        # generation archived with pins, compare the declaration
        # against the generation's actual archive-read traffic.
        # Warnings only: a discrepancy never flips the bundle to FAIL.
        if reconcile_fn is not None and resolve_archive_backend is not None:
            findings.extend(
                await _reconcile_findings(run_id, lines))
        return {
            "available": True,
            "ok": report.ok,
            "summary": report.summary(),
            "violations": sum(
                1 for f in findings
                if str(f["severity"]).lower() == "violation"
            ),
            "warnings": sum(
                1 for f in findings
                if str(f["severity"]).lower() == "warning"
            ),
            "findings": findings,
        }

    async def _reconcile_findings(run_id: str, lines: list[dict]
                                  ) -> list[dict]:
        """Warning-level findings from pin-vs-memload reconciliation."""
        backend = resolve_archive_backend(run_id)
        out: list[dict] = []
        generations: set[tuple[str, str]] = set()
        for line in lines:
            data = line.get("data", line)
            if not isinstance(data, dict):
                continue
            task_id = data.get("task_id")
            eid = data.get("execution_id")
            if task_id and eid:
                generations.add((task_id, eid))
        for task_id, eid in sorted(generations):
            try:
                report = await reconcile_fn(
                    backend, lines, task_id=task_id, eid=eid)
            except Exception:
                # Reconciliation is advisory: a backend failure must
                # never fail the validation read.
                continue
            if not getattr(report, "available", False):
                continue
            for finding in report.findings:
                if finding.kind == "eid_mismatch":
                    message = (
                        f"declared pin {finding.target_task_id}@"
                        f"{finding.declared_eid} but archive-read "
                        f"{finding.actual_eid}"
                    )
                elif finding.kind == "undeclared_load":
                    message = (
                        f"archive-read of {finding.target_task_id}@"
                        f"{finding.actual_eid} without a pin declaration"
                    )
                else:
                    message = (
                        f"pinned {finding.target_task_id}@"
                        f"{finding.declared_eid} never archive-read"
                    )
                out.append({
                    "severity": "warning",
                    "rule": f"DR-PIN-{finding.kind.upper()}",
                    "location": f"{task_id}@{eid}",
                    "message": message,
                })
        return out

    return router