"""Engine protocols are structural and runtime-checkable (api layer)."""

from __future__ import annotations

import asyncio

from ordigovernance.api import (
    DriftEngineProtocol,
    PinFindingShape,
    ReconcileReportShape,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeDriftEngine:
    def build(self, local_id, generations, statuses, *,
              audit_events, results):
        return {"verdict": "spy"}

    def compose_range(self, order, edges, node_reports, *,
                      baseline_rows, audit_events, node_pins,
                      pin_task_ids):
        return {"verdict": "spy-range"}


class _MissingCompose:
    def build(self, local_id, generations, statuses, *,
              audit_events, results):
        return None


def test_drift_engine_structural_match():
    assert isinstance(_FakeDriftEngine(), DriftEngineProtocol)


def test_drift_engine_missing_member_rejected():
    assert not isinstance(_MissingCompose(), DriftEngineProtocol)


class _FakeFinding:
    kind = "eid_mismatch"
    target_task_id = "a"
    declared_eid = "e-a1"
    actual_eid = "e-a2"


class _FakeReport:
    available = True
    findings = ()


class _NoFindings:
    available = True


def test_reconcile_shapes_structural_match():
    assert isinstance(_FakeFinding(), PinFindingShape)
    assert isinstance(_FakeReport(), ReconcileReportShape)
    assert not isinstance(_NoFindings(), ReconcileReportShape)

def test_reconcile_fn_structural_match():
    from ordigovernance.api import ReconcileFnProtocol

    async def _good(backend, audit_events, *, task_id, eid):
        return _FakeReport()

    assert isinstance(_good, ReconcileFnProtocol)
    assert not isinstance(object(), ReconcileFnProtocol)