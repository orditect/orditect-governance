"""Budget-exhaustion denial signature (pitfalls 18.3, live-verified).

A task submitted under a budget smaller than its LLM cost returns
ACCEPTED (the admission check runs at call time, not submit time),
then settles failed; the ledger is post-charge (pitfalls 13.11), so
the LLM call executes and OVERDRAWS it ("the last call overspends
honestly"), and every call after the overdraft blocks before
acquiring a slot, leaving no audit row. The three-part signature
this test pins:

  1. the task settles failed with result null;
  2. the audit stream shows the EXECUTED calls (tool_call +
     llm_call, the latter being the overdraft itself) and NO row for
     the first post-overdraft call (the archive write: no memsave);
  3. a direct call-plane request against the exhausted run answers
     409 admission denied BudgetExhaustedError with the full ledger
     state (scope / max_units / negative balance).

Cost arithmetic: the conftest scripted client charges 15 tokens per
call; the probe impl consumes 1 (search) + 15 (llm) = 16 units. A
budget of 10 lets BOTH calls start (the check keys on the balance
BEFORE each call, still positive) and guarantees the ledger is
overdrawn afterwards, so the archive write is blocked --
deterministically under both plausible check orderings
(balance-based or reserve-based).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.registry import (
    GatewayRegistry,
    ImplSpec,
    ToolSpec,
)

TERMINAL_WORDS = frozenset({"succeeded", "failed", "cancelled"})
BUDGET_MAX_UNITS = 10


async def _search_handler(query: str) -> dict:
    return {"query": query, "hits": 3}


class _BudgetProbeImpl:
    """Three governed stages: cheap tool read, LLM call, archive write."""

    def __init__(self, params: dict, surfaces: dict) -> None:
        pass

    async def run(self, ctx) -> dict:
        read, _ = await ctx.memoize(
            "search", 1, {"q": "budget probe"},
            lambda: ctx.tool_call(
                "search", "budget probe", purpose="search", seq=1,
                params={"q": "budget probe"}))
        analysis = await ctx.llm_call(
            "research", "analyze", 2,
            messages=[{"role": "user", "content": f"Analyze {read!r}"}])
        result = {"analysis": analysis["choices"][0]["message"]["content"]}
        # First call AFTER the overdraft: blocked by the exhausted
        # ledger, raises inside the impl, settles the task failed --
        # and writes NO audit row (pitfalls 13.11).
        await ctx.archive(result, pins={})
        return result


def _read_audit_rows(trace_dir: Path) -> list[dict]:
    path = trace_dir / "audit.ndjson"
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line).get("data", {}))
    return rows


def _wait_terminal(client: TestClient, run_id: str, task_id: str,
                   headers: dict, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/tasks/{task_id}",
                          headers=headers)
        assert resp.status_code == 200
        record = resp.json()
        if record["status"] in TERMINAL_WORDS:
            return record
        time.sleep(0.05)
    raise AssertionError(f"{task_id} never settled")


def test_budget_denial_three_part_signature(settings, auth_headers):
    registry = GatewayRegistry(
        tools={
            "search": ToolSpec(
                factory=lambda session: _search_handler,
                resource="web_search", event_type="tool_call",
                side_effect="readonly"),
        },
        impls={
            "budget_probe": ImplSpec(factory=_BudgetProbeImpl),
        },
    )
    app = build_app(settings, registry=registry)
    with TestClient(app) as c:
        # Start the run with a budget smaller than the impl's cost.
        resp = c.post("/runs", json={
            "run_id": "run-budget",
            "budget_max_units": BUDGET_MAX_UNITS,
        }, headers=auth_headers)
        assert resp.status_code == 201

        # Submission is ACCEPTED even though the budget can never
        # cover the run: the admission check runs at call time, not
        # submit time (pitfalls 18.3).
        resp = c.post("/runs/run-budget/tasks", json={
            "task_id": "budget-probe-1",
            "impl": "budget_probe",
            "params": {},
        }, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["accepted"] is True

        record = _wait_terminal(c, "run-budget", "budget-probe-1",
                                auth_headers)

        # Part 1: the task settles failed with result null.
        assert record["status"] == "failed"
        assert record["result"] is None

        # Part 2: the audit stream proves post-charge semantics. The
        # executed rows land before the task settles, but flush timing
        # is not a contract: poll briefly for the present-assertions.
        trace_dir = settings.trace_root / "run-budget" / "trace"
        event_types: list[str] = []
        deadline = time.time() + 5.0
        while time.time() < deadline:
            rows = _read_audit_rows(trace_dir)
            event_types = [str(row.get("event_type")) for row in rows]
            if "tool_call" in event_types and "llm_call" in event_types:
                break
            time.sleep(0.1)
        # The cheap call AND the LLM call EXECUTED: the LLM check
        # passed on a still-positive balance and overdrawn the ledger
        # ("the last call overspends honestly", 13.11).
        assert "tool_call" in event_types
        assert "llm_call" in event_types
        # The first call AFTER the overdraft (the archive write) is
        # blocked and leaves NO audit row: no memsave event anywhere
        # in the run's stream.
        rows = _read_audit_rows(trace_dir)
        assert not any(
            str(row.get("event_id", "")).startswith("memsave-")
            for row in rows)

        # Part 3: a direct call-plane request answers 409 with the
        # full ledger state.
        resp = c.post("/governed/llm-chat", json={
            "run_id": "run-budget",
            "client": "research",
            "purpose": "budget-check",
            "messages": [{"role": "user", "content": "hi"}],
        }, headers=auth_headers)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "admission denied" in detail
        assert "budget exhausted" in detail
        assert f"max_units={BUDGET_MAX_UNITS}" in detail
        assert "balance=-" in detail  # overdrawn, not merely zero