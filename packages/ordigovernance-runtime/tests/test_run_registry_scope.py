"""RunsRegistry.update_budget_scope: executor backfills the effective scope."""

from ordigovernance.runtime.lifecycle.run_registry import RunsRegistry, new_run_id


def test_update_budget_scope(tmp_path):
    reg = RunsRegistry(tmp_path / "runs")
    rid = new_run_id()
    reg.register_run(rid, "intent", {}, rid)  # placeholder = run_id
    reg.update_budget_scope(rid, "deep-research-root:deadbeef")
    entry = reg.get_run(rid)
    assert entry["budget_scope"] == "deep-research-root:deadbeef"
    assert entry["status"] == "running"  # other fields untouched


def test_update_unknown_run_is_noop(tmp_path):
    reg = RunsRegistry(tmp_path / "runs")
    reg.update_budget_scope("nope", "x:y")  # must not crash
    assert reg.get_run("nope") is None


def test_finish_run_preserves_scope(tmp_path):
    reg = RunsRegistry(tmp_path / "runs")
    rid = new_run_id()
    reg.register_run(rid, "i", {}, rid)
    reg.update_budget_scope(rid, "root:beef")
    reg.finish_run(rid, final_status="succeeded", budget_balance=42)
    assert reg.get_run(rid)["budget_scope"] == "root:beef"