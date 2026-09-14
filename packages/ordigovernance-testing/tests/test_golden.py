"""Golden bundle normalization: determinism and diff discipline."""

import json

from ordigovernance.testing.golden import (
    diff_bundles,
    normalize_bundle,
)


def _write_bundle(tmp_path, snaps, audit=(), deps=()):
    (tmp_path / "snapshots.ndjson").write_text(
        "\n".join(json.dumps(s) for s in snaps))
    (tmp_path / "audit.ndjson").write_text(
        "\n".join(json.dumps(a) for a in audit))
    (tmp_path / "deps.ndjson").write_text(
        "\n".join(json.dumps(d) for d in deps))


def _snap(task_id, eid, status, prevs=()):
    return {"data": {"task_id": task_id, "execution_id": eid,
                     "status": status,
                     "previous_execution_ids": list(prevs),
                     "started_at": 123.0, "elapsed_ms": 42}}


E1 = "11111111-1111-1111-1111-111111111111"
E2 = "22222222-2222-2222-2222-222222222222"


def test_normalization_replaces_eids_and_drops_volatile(tmp_path):
    _write_bundle(tmp_path, [
        _snap("plan", E1, "succeeded"),
        _snap("plan", E2, "succeeded", prevs=(E1,)),
    ], audit=[{"event_id": f"memsave-plan-{E2}-90",
               "payload": {"usage": {"total_tokens": 9},
                           "cost_units": 1}}])
    bundle = normalize_bundle(tmp_path)

    rows = bundle["snapshots.ndjson"]
    assert rows[0]["data"]["execution_id"] == "plan#1"
    assert rows[1]["data"]["execution_id"] == "plan#2"
    assert rows[1]["data"]["previous_execution_ids"] == ["plan#1"]
    # Volatile measurements are dropped, never compared.
    assert "started_at" not in rows[0]["data"]
    assert "elapsed_ms" not in rows[0]["data"]
    assert "usage" not in bundle["audit.ndjson"][0]["payload"]
    # Ids embedded in strings are rewritten too.
    assert bundle["audit.ndjson"][0]["event_id"] == \
        "memsave-plan-plan#2-90"


def test_two_runs_of_same_narrative_diff_empty(tmp_path):
    for run in ("a", "b"):
        run_dir = tmp_path / run
        run_dir.mkdir()
        _write_bundle(run_dir, [_snap("plan", E1, "succeeded")])
    golden = normalize_bundle(tmp_path / "a")
    current = normalize_bundle(tmp_path / "b")
    assert diff_bundles(golden, current) == []


def test_diff_reports_behavior_change(tmp_path):
    _write_bundle(tmp_path, [_snap("plan", E1, "succeeded")])
    golden = normalize_bundle(tmp_path)
    _write_bundle(tmp_path, [_snap("plan", E1, "failed")])
    current = normalize_bundle(tmp_path)
    diffs = diff_bundles(golden, current)
    assert any("status" in d for d in diffs)


def test_diff_whitelist_ignores_intentional_changes(tmp_path):
    _write_bundle(tmp_path, [_snap("plan", E1, "succeeded")])
    golden = normalize_bundle(tmp_path)
    _write_bundle(tmp_path, [_snap("plan", E1, "failed")])
    current = normalize_bundle(tmp_path)
    diffs = diff_bundles(golden, current,
                         ignore_patterns=[r"status"])
    assert diffs == []

def test_normalization_replaces_result_hash(tmp_path):
    _write_bundle(tmp_path, [_snap("plan", E1, "succeeded")],
                  audit=[{"event_id": f"memput-plan-{E1}-101",
                          "payload": {"memo_key": "memo/s/search/1/abc123",
                                      "result_hash": "0123456789ab",
                                      "first_write": True}}])
    bundle = normalize_bundle(tmp_path)
    payload = bundle["audit.ndjson"][0]["payload"]
    assert payload["result_hash"] == "hash#content"
    # Keys without eids and plain booleans stay comparable.
    assert payload["memo_key"] == "memo/s/search/1/abc123"
    assert payload["first_write"] is True