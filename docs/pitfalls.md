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

## 12. Split-specific lessons (orditect-components -> tiers)

### 12.1 Registry backfills over shared state are write-once
`RunsRegistry.update_budget_scope` must only backfill entries still
holding their placeholder (the run_id). The registry is shared
mutable state across interleaved runs (app + CLI on one hot path);
an unconditional overwrite lets a later run's scope derivation land
on an earlier run's entry, and every replay against the earlier run
then derives a memo scope it never used. This is the same lesson
class as 10.1/10.3: prefer per-run evidence over shared mutable
state, and guard every write-back. Locked by
tests/test_run_registry_scope.py.

### 12.2 LangChain shells must translate tool_calls in BOTH directions
ToolNode dispatches on LangChain ToolCall dicts ({name, args, id});
OpenAI endpoints require assistant history entries in the wire shape
({"function": {...}} plus tool messages with tool_call_id).
Translating only the human/system/ai text path breaks every real
react loop: the first round works, the second round's history is
malformed. The bridge's `_to_dicts` / `_to_ai_message` pair covers
both directions; `bind_tools` forwards specs as the endpoint-native
tools parameter on every tracked call. Locked by
tests/test_bridge_contract.py.

### 12.3 The passthrough tool schema must accept ToolNode's arg shape
A single-payload capture model requiring a "kwargs" field rejects the
way ToolNode invokes tools (the model's args dict passed directly).
The capture model carries extra="allow" and the invocation merges
top-level fields with the nested payload, so both call shapes land
identically on the tracked call. Same lesson class as the parent
project's "langchain swallows extra kwargs" pitfall — the schema is
a translation surface, not a validation surface.

### 12.4 ToolCall normalization is langchain-version-visible
langchain-core normalizes ToolCall dicts on AIMessage construction
(adding an explicit type field in newer releases). Emit the canonical
shape (with "type": "tool_call") from the translator instead of a
minimal dict, or round-trip assertions drift across langchain
versions.

## 13. Inherited lessons (components pitfalls + framework field notes)

Condensed from the orditect-components pitfall log and the orditect
framework's field notes, so users of this stack never need to
cross-read either. The parenthesized refs point at the source
documents for archaeology only.

### 13.1 Supervisor nodes must be real executor-managed nodes
(components 1.1/1.2) Dependency edges (declared structure) and
snapshot lineage (executed structure) are two projections; the
contextvar parent injection follows the EXECUTING node, not the
declared edge. A fan-out supervisor run inline inside another node's
execute() flattens the lineage tree, has zero snapshots and no
archive, and breaks fan-out replay and HITL retry on it. Hand-forging
its hot record (initialize + status write) masks the problem and
violates the lifecycle: hot records are written by the executor only.
Rule: every grouping node is a real submitted node.

### 13.2 Quality-gate reopen iterations never rebuild the producer
(components 1.6) Iteration >= 2 of QualityGatePattern goes through
sink.retry_scope, which REOPENS the existing record and re-executes
the SAME instance; build_producer / build_judge are invoked only on
iteration 1. Values captured in the build closure never reach the
reopened generation — feedback between rounds must travel through
the hot record or the archive (the acceptance ReviewImpl/WriterImpl
shape). Locked by test_quality_gate.py.

### 13.3 Derive round markers from the UPSTREAM hot record
(components 1.7) A judge's "which round am I judging" marker reads
the producer's previous_execution_ids (producer gen N settles before
judge gen N runs). Never derive it from the judge's own record: on a
resume its own prevs lag the producer's.

### 13.4 build_task runs BEFORE submit: no hot record, no eid at build time
(components 4.7/4.9) ReplayDriver._drive invokes build_task before
orchestrator.submit; the hot record (and therefore the eid) does not
exist yet. Anything reading hot.records[local_id] during construction
gets a KeyError or an empty dict. Logic needing the generation
identity lives in execute() (ctx.meta carries the eid); build_task
only returns the task shell. Async build_task callbacks are awaited
by the driver — fakes must mirror both timings.

### 13.5 Clean deterministic task ids from PREVIOUS runs' snapshots
(components 2.1, framework App.E.2) Deterministic ids +
if_not_exists=True silently reuse stale hot records from an
interrupted earlier run: no executor lifecycle, no snapshots, no
audit, and downstream consumes last run's results (run_rules reports
DR-DEP-001). The cleanup list is built from FACTS: compile-time-known
ids plus every task_id in PREVIOUS runs' snapshots.ndjson
(collect_known_task_ids). The current run's snapshot file does not
exist yet — scanning only it cleans nothing.

### 13.6 Expose HITL resources before driving; clear them at teardown
(components 3.1/3.2) Pause/resume must work DURING the run: hand
resources to the manager via the on_resources_ready hook, never by
assigning after the executor returns (every mid-run HITL call then
sees "no active run"). Clear them in finally — a stale reference
sends the next run's actions into a dead dispatcher queue: actions
are accepted, receipts never arrive. SingleRunManager implements both;
custom drivers must replicate them.

### 13.7 Receipts are dual and run-scoped; recover failed before blocking on cancelled
(components 3.3/1.4, framework App.E.3) The sink returns an
ACCEPTANCE receipt; the EXECUTION receipt is polled and 404 means
pending. The action queue is torn down when the run ends: a resume
issued after the run finishes is silently dropped. When a paused
child must be revived, the driver waits for SUCCESS, not for any
terminal state. And recover FAILED children (automatic path) before
blocking on cancelled ones (human path): pausing one child must not
freeze its siblings' recovery.

### 13.8 Recovery rebuilds from task_id alone: single construction source
(components 5.1/5.2, framework pitfall 3) RecoveryService's
task_factory cannot see the driver's constructor closures. Build
instances IDENTICALLY in the factory and in first-submission closures;
anything the factory cannot reconstruct from the task_id must be
fetchable at rebuild time — shared holder dicts filled at run start
(the acceptance app's holder pattern), never values captured before
the run context exists. A missing piece raises a loud KeyError.

### 13.9 REUSE vs RERUN is decided against the hot record's result
(framework App.E.4) Resume reuses a node only when its latest
generation is in reuse_terminal_words AND the hot record carries a
result; the result lands after execute() returns. A resume issued
while a sibling is mid-execution (inside a cooperative delay) RERUNS
it even though its expensive calls already succeeded and were billed.
Scope revivals narrowly (retry_scope the paused node) or accept the
rerun as correct semantics.

### 13.10 Cancel-token handlers must return envelopes, never bare None
(components pitfall 9, framework behavior boundary 1) The framework's
cancelled verdict is `result is None and token.is_cancelled()`: a
handler legitimately returning None while the token flips is
mislabeled cancelled. memory_read-style handlers return
{"key": ..., "value": None} envelopes. Applies to every custom
MemoBackend and tool handler.

### 13.11 Budget is post-charge; a blocked call leaves no audit row
(framework behavior boundaries 2/6) check() blocks when balance <= 0,
so the last call overspends honestly and every subsequent call blocks
BEFORE acquiring a slot — and writes NO audit event (it never reached
the resource). Never reconcile spend from missing rows. Stub routing
decisions bypass the call plane by the same design and must write
audit directly (GovernedToolSet.record_stub_decision is the
reference).

### 13.12 snapshot_sink is the observability master switch
(framework field note 2.9) An orchestrator built without
snapshot_sink writes ZERO snapshots with no error raised; every
viewer, validate and recovery read sees nothing. build_run_context
wires ProtocolSnapshotSink(store.snapshot); custom assembly must do
the same.

### 13.13 Same-resource nesting is exempt: distinct names for real contention
(framework field note 2.2) A child whose resource_type matches an
ancestor's inherits the ancestor's semaphore slot (lineage exemption,
the no-self-deadlock mechanism): no real contention occurs. To make
workers actually queue, register and use DISTINCT resource names
(root on task_execution, workers on worker_exec). Registration before
first acquire stays mandatory (pitfall 2).

### 13.14 Single-active-run guards are process-local
(components 10.4) SingleRunManager serializes runs INSIDE one
process. A second process (a CLI) sharing the same redis hot path and
registry races the first: both sides' cleanup passes delete each
other's records and both snapshot sinks mix generations. Multi-process
drivers check the registry for status == "running" entries before
starting, with an explicit force flag for stale entries left by a
crashed run.

### 13.15 Every process writing memo/archive traffic configures persistence
(components 10.2) The mock memory body is process-local until
configure_memory_body(path) is called; a process that never calls it
loses every memo entry and gen-result archive at exit, and replays
against its runs see an empty store. Every entry point (app, CLI,
drivers) configures the body before driving.

### 13.16 On an unexpected verdict, diff the archived results first
(components 10.6) The cheapest first move on any non-identical drift
verdict is to dump both generations' archived results and diff them
field by field; the differing field names the mechanism (provenance,
missing baseline, cross-run contamination) faster than reading any
code path.

### 13.17 "no snapshot" is a symptom, never filter it away
(components 6.2) A graph node without snapshots signals a stale hot
record (13.5) or an inline-degraded supervisor (13.1). Rendering it
as "no snapshot" keeps the bug visible; filtering such nodes out of
the graph hides it.

### 13.18 UI components carry zero business vocabulary
(components 6.1) Task-id prefixes, root ids, token-attribution
regexes and event-type names are injected from the app's assembly
file. The viewer's ui/ components take them as parameters
(tokenCallers, workerPrefix, governedTypes, formatNodeLabel); nothing
business-shaped is hardcoded in the reusable layer.