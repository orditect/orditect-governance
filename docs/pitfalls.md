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

## 14. Bridge bring-up: real-endpoint verification lessons

### 14.1 Probe the bare client contract before any react loop
GovernedLLMClient lives outside this repository; whether it forwards
the endpoint-native tools parameter and returns usage is not
observable from any test here. A missing tools forward fails
SILENTLY: the model answers in plain text and no error ever
surfaces. scripts/probe_bridge_env.py runs three layers (bare client
-> langgraph react loop -> deepagents agent) so a breakage attributes
to exactly one component instead of surfacing three frames away
inside a react loop (same class as pitfall 6). Rule: never debug a
real react loop before layer 1 is green.

### 14.2 Bridge shells must forward every call option, not only messages
A shell that drops stop sequences or bind-time options
(parallel_tool_calls, ...) changes model behavior without any error.
LangChainTrackedLLM merges bind kwargs, tool specs, tool_choice and
stop into every tracked call; the unbound model never carries them
(clone discipline). Locked by tests/test_bridge_params.py.

### 14.3 tool_calls survive in whichever shape the middleware left them
Middleware chains (deepagents) may leave raw OpenAI tool_calls only
under additional_kwargs["tool_calls"] instead of normalizing into
.tool_calls. Dropping them makes the next round's history reference
tool_call_ids the assistant message never declared, and the endpoint
rejects the request with a 400. _raw_tool_calls reads both shapes;
already wire-shaped entries pass through untouched. Locked by
tests/test_bridge_params.py.

### 14.4 Real models require an explicit args_schema per tool
The passthrough capture schema advertises a single opaque "kwargs"
object; a real model fills parameters unreliably against it, and
strict endpoints reject additionalProperties schemas. The passthrough
schema is for deterministic tests only. Tool payload keys must also
avoid the reserved plumbing names (name / inputs / reuse /
record_origin / purpose / seq / params / call_id / content_fn /
payload_fn): PassthroughTrackedToolSet.call fails loudly with a
rename instruction instead of surfacing as a TypeError frames away.
Locked by tests/test_bridge_params.py.

### 14.5 Fakes must mirror reopen semantics: previous_status and a cleared result
An engine memo layer reads previous_status for on_resume routing; a
fake that never writes it makes on_resume unreachable in every
fixture while production behaves differently. A reopened record that
keeps its old result serves the PREVIOUS generation's outputs while
the new generation is still pending. MemoryTaskStorage.reopen_task
writes previous_status and clears result/cancel_requested. Same
lesson class as pitfall 1: read the framework's real behavior, not
the minimal surface.

### 14.6 A receipt wait that times out silently is a 300-second misdiagnosis
QualityGatePattern._wait_receipt used to return silently on timeout;
the gate then blocked on wait_terminal for a generation that was
never reopened and reported a step timeout one frame away from the
cause. Receipt waits raise TimeoutError (matching
FanOutPattern.wait_resumed and ReplayDriver._wait_reopened).

### 14.7 The shared viewer API client lists only routes that exist
compare.js fetched /api/runs/{id}/results, a route the trace router
never shipped, so every compare open 404'd — and the payload was
never even rendered. Frame discipline for the graph panel: a render
request arriving while mermaid is busy must coalesce to the latest
definition, never drop (a dropped frame leaves the DAG one poll
behind until the next signature change).

### 14.8 Reopen chains died at re-initialize, not at reopen (FIXED upstream)
Probe (scripts/probe_reopen_semantics.py) against real TaskRedisDB
showed: reopen_task preserves previous_execution_ids, writes
previous_status and clears result; initialize_task on an EXISTING
record reset the chain unconditionally. A manual reopen followed by
orchestrator.submit therefore lost the whole generation chain on real
redis while the in-memory fixture (setdefault-based initialize)
silently kept it. FIXED in orditect v0.1.8: submit() is schedule-only
for PENDING existing records (scheduled WITHOUT re-initialization);
the storage-layer reset semantics remain for explicit resets.

### 14.9 The reopen+resubmit contract: never if_not_exists=True
Orchestrator.submit(if_not_exists=True) is a full idempotent skip:
the task is never registered for execution. After reopen_task,
resubmit WITHOUT if_not_exists (default False): the record is PENDING
(reopen_task set it), so the v0.1.8+ schedule-only path preserves the
chain AND executes. Earlier versions of this repo's examples carried
an update_task(status="pending") workaround plus an if_not_exists
experiment that deadlocked the run; both removed once the framework
fix landed. Storage-layer initialize_task(if_not_exists=False) still
resets by design (explicit reset path) -- do not call it on a
reopened record.

## 15. Real-endpoint verification lessons (probe bring-up)

### 15.1 Optional configuration loaders must never skip silently
The probe treated python-dotenv as optional and skipped .env loading
silently when the package was absent: a .env sitting at the repo root
read as zero keys, and the failure message blamed missing variables
instead of the unloaded file. One full debugging round went into
"the file is right there, why is it failing". Rule: an expected
configuration source reports what it did on startup (path, parser,
key count); optional dependencies degrade to a built-in fallback,
never to silence. Locked by the startup diagnostic line in
scripts/probe_bridge_env.py.

### 15.2 Framework factory signatures drift; the smoke test is the lock
deepagents renamed instructions to system_prompt at 0.7 (no **kwargs
fallback: a wrong name raises TypeError at assembly time), and
LangGraph V1 moved create_react_agent into langchain.agents as
create_agent (prompt renamed system_prompt). Both drifts were caught
by the real-factory smoke test, not by reading changelogs. The
three-part lock: (a) test_real_factory_signature_smoke assembles a
real graph against the installed package; (b) pyproject extras
floors sit at the renamed version (deepagents>=0.7); (c) dual-import
tries the new home first. Bridge kwargs pass through verbatim, so
callers must use the name their installed version declares.

### 15.3 Never hand a raw string to a framework factory's model slot
create_deep_agent(model=...) accepts str | BaseChatModel. Passing a
string makes the framework build its own LLM client entirely outside
the governed plane: no semaphore, no budget, no audit, no call_id
discipline, and nothing raises — the calls just never appear in the
evidence chain. The tracked atom is the entire governance surface;
the model slot only ever receives a LangChainTrackedLLM. Same
lesson class as 12.2 (the bridge shell IS the governance boundary).

### 15.4 Transitive-dependency warnings are not library bugs
starlette's testclient module references anyio's deprecated
BlockingPortal alias at import time; upgrading to the newest
fastapi/starlette does not remove it (verified present in starlette
1.6.0 with anyio 4.15.1 — upstream has not migrated). Discipline:
(a) never declare a transitive dependency in pyproject — fastapi
pins starlette's range and a second constraint risks an unsolvable
intersection; (b) upgrade the DIRECT dependency (fastapi), which
manages the transitive range — upgrading starlette alone stops at
fastapi's ceiling; (c) silence with a filterwarnings entry pinned to
the exact message + module so every other warning stays visible, and
record the verified versions in the comment for later removal.
Related micro-lesson: check versions with importlib.metadata.version
or pip show — several packages (anyio) expose no __version__
attribute.

## 16. Gateway (n8n bridge) lessons

### 16.1 Dynamic registry loading bypasses the static import gate
scripts/check_import_boundary.py scans only STATIC imports inside
packages/*/src. The gateway loads impl/tool/composite factories at
boot through entry points and GATEWAY_REGISTRY_MODULE -- invisible to
the gate. A deployment pointing the module channel at a closed-tier
package would smuggle forbidden imports past CI. The gateway
re-checks every loaded factory's __module__ against the forbidden
namespaces at boot and REFUSES to start on a hit, listing the
offender (registry.validate_registry). Rule: any runtime-loaded
extension point needs its own boot-time boundary check; static gates
only cover static imports.

### 16.2 The ambient run coexists with the single-active guard
Call-plane requests without a run_id need somewhere to land, but
opening a user run for them would serialize all such calls behind
the single-active guard. The gateway opens one ambient run at boot
(run_id "ambient", never finished, torn down at shutdown) and routes
run_id-less calls there; it is NOT counted as the active run, so a
user run can start while ambient traffic flows. Both share the
process-wide hot path: semaphore queueing stays process-global, which
is correct (one backend, one resource reality). Ambient budget
exhaustion degrades honestly to 409, never silently.

### 16.3 Composites are drive-level drivers, never supervisor nodes
A composite (quality gate, fan-out driver) must NOT be packaged as a
supervisor node inside the executor: pitfall 13.1 applies -- inline
fan-outs flatten the lineage tree and produce zero snapshots. The
gateway runs composites as background asyncio tasks inside the
session; children submit with parent_task_id=run root and evidence
closes through the child tasks. Child attribution uses a contextvar
set inside the composite's own task (_COMPOSITE_CTX): concurrent
composites stay isolated, and submit_task/register_descriptor pick up
the attribution transparently. A composite failure lands on the
composite's own status endpoint, never on the run.

### 16.4 Composite children register descriptors BEFORE the pattern submits
Patterns like QualityGatePattern perform the first submission
themselves, so the gateway's submit_task path never sees their
children -- but the action sink's reopen path and HITL retry rebuild
tasks through task_factory, which resolves only REGISTERED
descriptors. Composite drivers therefore call
session.register_descriptor() up front (validation + duplicate check,
no submission) and build instances through session.assemble_task(),
keeping one construction source for first submit and recovery alike
(pitfall 13.8). Registering without submitting is the composite-side
half of the single-source rule.

### 16.5 Hot reads close at finish; evidence reads belong to the cold path
The gateway serves hot-record reads (GET /runs/{id}/tasks/{tid}) only
while the run is active; after finish they 404. This is deliberate:
the run context is torn down at finish, and answering historical
reads from a dead session would resurrect the pitfall 13.6 shape
(actions into a dead dispatcher). Historical evidence is the viewer's
job over the shared trace_root (examples/gateway_n8n/viewer_app.py).
One gateway process = write path; the viewer = read path.

### 16.6 D7 dedup is run-scoped; the hot path is not the duplicate table
submit_task originally rejected any task_id with an existing hot
record; the hot path is shared across runs, so a record left by a
PREVIOUS run 409'd the next run's submission (field-verified:
`task 'researcher-m3' is already submitted in run 'run-...'` on a
brand-new run). The duplicate guard now keys on the session's
descriptor registry -- same-run duplicates still 409, and the run
root id is rejected with the same verdict. A cross-run id collision
is a fresh generation on a stale record; the old run's evidence
stays in its own cold path. Clients needing cross-run uniqueness
must mint unique ids (the n8n nodes suffix the execution id).
Locked by test_task_id_from_a_finished_run_does_not_block_a_new_run.

### 16.7 Run-scoped reads must not pass through to the global hot path
GET /runs/{id}/tasks/{tid} read the shared hot storage directly, so
a NEW run returned a PREVIOUS run's record (field-verified: a fresh
run answered with another run's cancelled record and its old
execution_id). The same leak existed on the call plane:
allocate_call_identity attributed calls to any hot record. Both
paths now resolve ownership through the session's descriptor
registry (plus the run root) before touching the hot path; the
ambient run is exempt by design (D2 catch-all attribution bucket).
Locked by test_task_reads_are_run_scoped_over_the_shared_hot_path
and test_call_plane_task_attribution_is_run_scoped.

### 16.8 Direct (non-sink) actions owe the client a receipt too
HITL retry is deliberately direct (reopen + resubmit, no sink
action), but it returned a fabricated `retry-direct-*` action_id the
receipt endpoint had never heard of: clients polling it got 404
forever and read the action as pending (the n8n Approval node would
have waited the full poll timeout). The gateway now records the
execution receipt synchronously at accept time -- the reopen +
resubmit has already happened -- and the receipt endpoint serves it
as a fallback after the sink table. Locked by
test_retry_receipt_is_served_for_direct_actions.

### 16.9 accepted != executed when the dispatcher is dead (framework-level hazard)
Field incident: a pause settled the task cancelled; two resumes then
returned accepted=true and no generation ever reran; the receipt
endpoint later 404'd (run finished). The action queue was dead
while the run still looked active -- pitfall 13.6's shape one layer
down. The route-level guard (404 on non-active runs) cannot see a
dead dispatcher inside a live session, and the sink's accepted flag
only means "queued". The only truth is the EXECUTION receipt plus
the hot record's previous_execution_ids. Client discipline: never
treat accepted as evidence; poll the receipt AND the record (the
n8n node's waitForReceipt + awaitDecision shape). Detection recipe:
receipt never arrives AND prevs unchanged after the expected
latency -> the dispatcher is dead; finish and restart the run.