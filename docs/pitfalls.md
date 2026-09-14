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

## 7. Editable installs and IDE red herrings

Setuptools' default editable mode (import hooks, not .pth paths)
confuses PyCharm/VSCode into "Cannot find reference" for PEP 420
namespaces. Verify with `python -c "import ..."` first — if the
runtime import works, it is an IDE indexing issue, not packaging.
Fix: `pip install -e ... --config-settings editable_mode=compat`,
or mark each package's `src/` as Sources Root. Never "fix" code to
satisfy an IDE error that the interpreter does not share.

## 8. Protocol isinstance checks need @runtime_checkable

`isinstance(x, SomeProtocol)` raises `TypeError` at runtime unless
the protocol is decorated with `@runtime_checkable`. The api
package's engine plug-in protocols (MemoLayerProtocol,
PolicyResolverProtocol) are explicitly structural-check targets —
keep the decorator on every protocol that tests assert against.