# Pitfalls & discipline notes (ordigovernance)

Hard-won lessons from splitting orditect-components into the open
ordigovernance stack and bringing up the first real-executor acceptance
ground. Every item below cost a debugging round; read before
extending the runtime, the testing fixtures, or the examples.

## 1. Fakes must mirror the FULL framework contract surface

The acceptance ground was this project's first end-to-end run over
the real TaskOrchestrator (every previous test was fake-driven).
Each fix exposed the next layer of the storage/governor/quota
contract the fakes were missing — in this exact order:

1. `initialize_task` needs `parent_task_id` and `if_not_exists`
   (lifecycle.initialize).
2. `update_task` needs `validate_status_transfer` AND the
   `updates={...}` keyword shape — the executor calls
   `update_task(task_id, updates={...}, validate_status_transfer=False)`,
   so a fake accepting only a positional `patch` dict silently never
   writes the terminal status and `wait_terminal` hangs forever.
   **A hang after "Task succeeded" in the executor log means the
   storage fake swallowed the terminal write.**
3. The semaphore registry must contain every resource the executor
   and the governed clients ask for, including `task_execution`
   (the GovernedAgent default resource_type) — `get_semaphore`
   returning None surfaces as `'NoneType' object has no attribute
   'acquire'` two frames away from the real cause.
4. Semaphore tokens must be objects with a `.value` attribute
   (TaskbaseGovernorAdapter keys its internal token map on
   `token.value`); bare strings fail with
   `'str' object has no attribute 'value'`.
5. `MemoryQuota.reserve_units` must mirror the redis contract:
   keyword-only `(scope, task_id, units, max_units, task_ttl_sec)`,
   `{"ok": bool, "reason": str, ...}` responses, `already_reserved`
   idempotency, and real `max_units` enforcement.

**Rule**: before writing a fake, read the framework's real call
sites (`grep -n "self.storage\."`, `inspect.signature`), not the
protocol docs. A fake that is one method short does not fail at
construction — it fails three frames later inside the executor.

## 2. Registered resource names and semaphore names are one table

`GovernedToolSet.register(resource=...)`, the agents'
`resource_type`, and `build_memory_hot_path({name: limit})` all
reference the SAME namespace. A tool registered with
`resource="web_search"` while the registry only has `llm_research`
fails with the same opaque acquire-None error. **Rule**: when
adding a tool or an agent, add its resource to the hot-path
semaphore table in the same edit, and verify with
`list(registry._sems.keys())` before running.

## 3. Tool registration is business assembly, never automatic

`GovernedToolSet(...)` with only memory handlers knows exactly two
tools (`memory_read`, `memory_write`). Business tools must be
registered explicitly (`TOOL_SPECS` loop), exactly as every
orditect-components entry point did. `KeyError: unknown governed
tool 'search'` means the registration loop was skipped, not that
the tool layer broke.

## 4. retry_scope requires a real terminal scope root

`retry_scope(root_id, {target})` reopens the root plus every
ancestor on the path and rejects non-terminal ancestors. If the
root id exists only as an edge fact (never initialized as a task
record), the reopen hangs. **Rule**: initialize the scope root as a
real terminal record before any pattern that reopens through it
(`initialize_task(ROOT_ID, initial_status="succeeded")`), or reopen
the target directly. Same lesson class as components pitfall 1.3.

## 5. Manual reopen + submit is silently deduped

`storage.reopen_task(tid)` followed by `orchestrator.submit(...,
if_not_exists=True)` is a no-op: the record exists, so the
idempotent submission skips it, and `wait_terminal` then waits on a
generation that will never execute. **Rule**: rerun a settled node
through the sink's scope retry (the HITL path) or reopen+resubmit
with the executor's real semantics — never hand-roll the sequence.

## 6. Diagnose hangs by direct execution, not pytest

`pytest -q` inside `timeout` prints nothing when killed mid-test.
The fastest loop is:

    PYTHONPATH=. timeout 40 python -c "
    import asyncio, logging, tempfile
    from pathlib import Path
    logging.basicConfig(level=logging.INFO)
    from examples.acceptance.app import execute_acceptance_run
    asyncio.run(asyncio.wait_for(
        execute_acceptance_run(Path(tempfile.mkdtemp())/'trace'),
        timeout=35))" 2>&1 | tail -30

The last log line before the timeout IS the stuck await. Two
iterations of this located every issue above faster than any
reading of pytest output.

## 7. PEP 420 namespace packages require ONE editable mode everywhere

Setuptools' default editable mode (import hooks, not .pth paths)
both confuses IDE indexing ("Cannot find reference") and is fragile
for namespace subpackages: mixing strict (default) and compat
installs across the eight `ordigovernance-*` distributions silently
makes some subpackages unimportable at runtime
(`ModuleNotFoundError: ordigovernance.runtime` while
`pip list` shows it installed). Verify with `python -c "import ..."`
first — if the runtime import works, an IDE error is indexing, not
packaging. **Rule**: install ALL governance packages with
`--config-settings editable_mode=compat`; never mix modes inside one
namespace; the `orditect-*` framework packages are an independent
namespace and can stay on the default mode.

## 8. Protocol isinstance checks need @runtime_checkable

`isinstance(x, SomeProtocol)` raises `TypeError` at runtime unless
the protocol is decorated with `@runtime_checkable`. The api
package's engine plug-in protocols (MemoLayerProtocol,
PolicyResolverProtocol) are explicitly structural-check targets —
keep the decorator on every protocol that tests assert against.

## 9. Sink action rerun semantics are status-dependent

`retry_scope(root, {target})` reruns only FAILED nodes; `resume_tree`
reruns failed/cancelled nodes and reuses succeeded ones. Neither
reruns a SUCCEEDED node. A receipt returning successfully never
implies a rerun happened — the receipt's `reuse=/rerun=` tallies are
the only truth. HITL resume of a cancelled node uses `resume_tree`;
a deliberate second generation of a succeeded node uses direct
`reopen_task + submit`. Locked by the acceptance ground's two beats.

## 10. Drive-layer fan-outs must pass parent_task_id explicitly

Sink tree actions (resume_tree / retry_scope) walk the SNAPSHOT
parentage, not the declared dependency edges. Inside an executing
supervisor node the executor's contextvar injects the parent
automatically (pitfall 1.1); a fan-out driven directly from the
drive layer has no executing node, so every child is submitted with
`parent=None` and tree actions walk an empty tree (receipt says
`rerun=0`, the workflow hangs downstream). FanOutPattern accepts
`parent_task_id` for exactly this case; inside a node it must stay
None. Locked by test_fanout.py.

## 11. selfcheck trace_dir must be a SUBDIRECTORY of the temp root

`build_run_context` cleans `trace_dir.parent`. If the caller passes a
bare `tempfile.mkdtemp()` path as trace_dir, the parent is /tmp and
the NEXT run's cleanup silently deletes the PREVIOUS run's bundle
before any diff — the diff then reads an empty directory and reports
phantom "0 != 45" divergences. Rule: trace_dir = mkdtemp()/"trace".
Locked by examples/acceptance/selfcheck.py.