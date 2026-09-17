"""Task-plane endpoints: lifecycle, descriptors, evidence, vocabulary."""

from __future__ import annotations

import json
import time


def _wait_terminal(client, run_id, task_id, headers, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/tasks/{task_id}",
                          headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"{task_id} never settled")


def _trace_dir(settings, run_id):
    return settings.trace_root / run_id / "trace"


def _start_run(client, headers, run_id="run-1"):
    resp = client.post("/runs", json={"run_id": run_id},
                       headers=headers)
    assert resp.status_code == 201
    return resp.json()["run_id"]


def test_start_run_and_single_active_guard(registry_client, auth_headers):
    _start_run(registry_client, auth_headers)
    resp = registry_client.post("/runs", json={"run_id": "run-2"},
                                headers=auth_headers)
    assert resp.status_code == 409
    assert "run-1" in resp.json()["detail"]


def test_submit_task_polls_terminal_and_archives(registry_client,
                                                 auth_headers,
                                                 settings):
    run_id = _start_run(registry_client, auth_headers)
    resp = registry_client.post(
        f"/runs/{run_id}/tasks",
        json={"task_id": "task-a", "impl": "echo",
              "params": {"marker": "hello"}},
        headers=auth_headers)
    assert resp.status_code == 201
    body = _wait_terminal(registry_client, run_id, "task-a",
                          auth_headers)
    assert body["status"] == "succeeded"
    assert body["result"] == {"marker": "hello"}
    assert body["execution_id"]

    # The generation archives itself: memsave at the archive band.
    audit_path = _trace_dir(settings, run_id) / "audit.ndjson"
    rows = [json.loads(x) for x in audit_path.read_text().splitlines()
            if x.strip()]
    event_ids = [r.get("data", r).get("event_id", "") for r in rows]
    assert any(eid.startswith("memsave-task-a-")
               and eid.endswith("-90") for eid in event_ids)


def test_dependency_edges_written_for_upstream(registry_client,
                                               auth_headers,
                                               settings):
    run_id = _start_run(registry_client, auth_headers)
    registry_client.post(f"/runs/{run_id}/tasks",
                         json={"task_id": "task-a", "impl": "echo",
                               "params": {}},
                         headers=auth_headers)
    registry_client.post(f"/runs/{run_id}/tasks",
                         json={"task_id": "task-b", "impl": "echo",
                               "params": {}, "upstream": ["task-a"]},
                         headers=auth_headers)
    _wait_terminal(registry_client, run_id, "task-b", auth_headers)

    deps_path = _trace_dir(settings, run_id) / "deps.ndjson"
    rows = []
    for line in deps_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(row.get("data", row))
    edges = {(r.get("child_id"), r.get("parent_id")) for r in rows}
    # child depends on parent: task-b declares task-a as its parent (D1).
    assert ("task-b", "task-a") in edges


def test_submit_unknown_impl_422_lists_vocabulary(registry_client,
                                                  auth_headers):
    run_id = _start_run(registry_client, auth_headers)
    resp = registry_client.post(f"/runs/{run_id}/tasks",
                                json={"task_id": "t", "impl": "nope"},
                                headers=auth_headers)
    assert resp.status_code == 422
    assert "echo" in resp.json()["detail"]


def test_submit_unknown_tool_whitelist_422(registry_client, auth_headers):
    run_id = _start_run(registry_client, auth_headers)
    resp = registry_client.post(f"/runs/{run_id}/tasks",
                                json={"task_id": "t", "impl": "echo",
                                      "tools": ["nope"]},
                                headers=auth_headers)
    assert resp.status_code == 422
    assert "search" in resp.json()["detail"]


def test_submit_duplicate_task_409(registry_client, auth_headers):
    run_id = _start_run(registry_client, auth_headers)
    registry_client.post(f"/runs/{run_id}/tasks",
                         json={"task_id": "task-a", "impl": "echo",
                               "params": {}},
                         headers=auth_headers)
    resp = registry_client.post(f"/runs/{run_id}/tasks",
                                json={"task_id": "task-a",
                                      "impl": "echo", "params": {}},
                                headers=auth_headers)
    assert resp.status_code == 409
    assert "task-a" in resp.json()["detail"]

def test_task_id_from_a_finished_run_does_not_block_a_new_run(
        registry_client, auth_headers):
    # D7 is run-scoped: a record left on the shared hot path by a
    # finished run must not 409 the next run's submission
    # (docs/pitfalls.md 16.6).
    run1 = _start_run(registry_client, auth_headers, "run-prev")
    registry_client.post(f"/runs/{run1}/tasks",
                         json={"task_id": "task-a", "impl": "echo",
                               "params": {}},
                         headers=auth_headers)
    first = _wait_terminal(registry_client, run1, "task-a",
                           auth_headers)
    assert first["status"] == "succeeded"
    resp = registry_client.post(f"/runs/{run1}/finish",
                                headers=auth_headers)
    assert resp.status_code == 200

    run2 = _start_run(registry_client, auth_headers, "run-next")
    resp = registry_client.post(f"/runs/{run2}/tasks",
                                json={"task_id": "task-a",
                                      "impl": "echo", "params": {}},
                                headers=auth_headers)
    assert resp.status_code == 201
    second = _wait_terminal(registry_client, run2, "task-a",
                            auth_headers)
    assert second["status"] == "succeeded"


def test_task_reads_are_run_scoped_over_the_shared_hot_path(
        registry_client, auth_headers):
    # The hot path is process-global; a record existing under a task
    # id is not proof the task belongs to the addressed run
    # (docs/pitfalls.md 16.7).
    run1 = _start_run(registry_client, auth_headers, "run-prev")
    registry_client.post(f"/runs/{run1}/tasks",
                         json={"task_id": "task-a", "impl": "echo",
                               "params": {}},
                         headers=auth_headers)
    _wait_terminal(registry_client, run1, "task-a", auth_headers)
    registry_client.post(f"/runs/{run1}/finish", headers=auth_headers)

    run2 = _start_run(registry_client, auth_headers, "run-next")
    resp = registry_client.get(f"/runs/{run2}/tasks/task-a",
                               headers=auth_headers)
    assert resp.status_code == 404


def test_call_plane_task_attribution_is_run_scoped(registry_client,
                                                   auth_headers):
    # Same ownership discipline on the call plane (16.7): a user run
    # must not attribute calls to a record owned by a previous run.
    run1 = _start_run(registry_client, auth_headers, "run-prev")
    registry_client.post(f"/runs/{run1}/tasks",
                         json={"task_id": "task-a", "impl": "echo",
                               "params": {}},
                         headers=auth_headers)
    _wait_terminal(registry_client, run1, "task-a", auth_headers)
    registry_client.post(f"/runs/{run1}/finish", headers=auth_headers)

    run2 = _start_run(registry_client, auth_headers, "run-next")
    resp = registry_client.post(
        "/governed/llm-chat",
        json={"run_id": run2, "task_id": "task-a", "client": "research",
              "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_headers)
    assert resp.status_code == 404

def test_get_task_unknown_404_and_non_active_run_404(registry_client,
                                                     auth_headers):
    run_id = _start_run(registry_client, auth_headers)
    resp = registry_client.get(f"/runs/{run_id}/tasks/ghost",
                               headers=auth_headers)
    assert resp.status_code == 404
    resp = registry_client.get("/runs/nope/tasks/x", headers=auth_headers)
    assert resp.status_code == 404


def test_finish_run_records_registry_and_closes_reads(registry_client,
                                                      auth_headers):
    run_id = _start_run(registry_client, auth_headers)
    registry_client.post(f"/runs/{run_id}/tasks",
                         json={"task_id": "task-a", "impl": "echo",
                               "params": {}},
                         headers=auth_headers)
    _wait_terminal(registry_client, run_id, "task-a", auth_headers)

    resp = registry_client.post(f"/runs/{run_id}/finish",
                                headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["final_status"] == "succeeded"

    # The registry entry carries the finish facts.
    entry = registry_client.get(f"/runs/{run_id}",
                                headers=auth_headers).json()
    assert entry["status"] == "finished"
    assert entry["final_status"] == "succeeded"

    # After finish the run is no longer active: hot reads close (D14).
    resp = registry_client.get(f"/runs/{run_id}/tasks/task-a",
                               headers=auth_headers)
    assert resp.status_code == 404


def test_finish_with_running_task_409_lists_it(registry_client,
                                               auth_headers):
    run_id = _start_run(registry_client, auth_headers)
    registry_client.post(f"/runs/{run_id}/tasks",
                         json={"task_id": "slow-task", "impl": "slow",
                               "params": {"delay": 1.0}},
                         headers=auth_headers)
    resp = registry_client.post(f"/runs/{run_id}/finish",
                                headers=auth_headers)
    assert resp.status_code == 409
    listed = resp.json()["detail"]["non_terminal_tasks"]
    assert [t["task_id"] for t in listed] == ["slow-task"]

    # Once settled, finish succeeds.
    _wait_terminal(registry_client, run_id, "slow-task", auth_headers)
    resp = registry_client.post(f"/runs/{run_id}/finish",
                                headers=auth_headers)
    assert resp.status_code == 200


def test_vocabulary_available_on_ambient_for_design_time(registry_client,
                                                         auth_headers):
    # n8n nodes query the vocabulary at design time, before any user
    # run exists; the registry is process-global, so ambient answers.
    resp = registry_client.get("/runs/ambient/vocabulary",
                               headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert {e["name"] for e in body["impls"]} == {"echo", "slow"}
    assert {e["name"] for e in body["tools"]} == {"search"}


def test_get_run_lists_task_states_while_active(registry_client,
                                                auth_headers):
    run_id = _start_run(registry_client, auth_headers)
    registry_client.post(f"/runs/{run_id}/tasks",
                         json={"task_id": "task-a", "impl": "echo",
                               "params": {}},
                         headers=auth_headers)
    _wait_terminal(registry_client, run_id, "task-a", auth_headers)
    entry = registry_client.get(f"/runs/{run_id}",
                                headers=auth_headers).json()
    assert entry["status"] == "running"
    assert [(t["task_id"], t["status"]) for t in entry["tasks"]] == \
        [("task-a", "succeeded")]