# orditect-governance — Design Goals

Audience: external teams extending, optimizing, or building on this
repository. This document states what the project is FOR, the
invariants that keep it coherent, and the discipline expected from
every contribution. For hard-won operational lessons see
docs/pitfalls.md; for the split rationale see README.md ("Tier map").

## 1. Mission

Make any agentic workflow **auditable, replayable and governable** —
regardless of which orchestration framework drives it.

orditect-governance is the open component layer over the orditect
framework. It turns the framework's primitives (reopen generations,
semaphores, budget ledger, trace bundle) into a reusable governance
stack: governed agents and tools, an evidence chain (archive, pins,
audit), lifecycle helpers, orchestration patterns, replay mechanics,
and a cold-path viewer.

## 2. Design goals

### G1 — Two governance planes, explicitly separated

- **Call level**: every atomic call (tool, LLM, memory) is a governed
  unit — semaphore, budget, call_id idempotency, audit event, content
  pointer-ization.
- **Task level**: every node is a governed generation — state machine,
  execution ids, lineage pins, dependencies, HITL, snapshot evidence.

Neither plane may silently absorb the other. A contribution that adds
task-level state to a call-level object (or vice versa) is a design
violation, however convenient it looks.

### G2 — Mechanism-direct open tier

The open runtime enforces every call and every generation but carries
**no engine intelligence**: no memo reuse strategy, no replay policy
routing, no drift attribution. The default behavior is the identity
(passthrough): identical inputs across generations re-execute and
re-charge, making cost explicit by construction.

Engines plug in behind the api protocols at assembly time
(`MemoLayerProtocol`, `PolicyResolverProtocol`, drift engine). The
test for any new feature: **does it change routing/reuse/attribution
semantics? Then it belongs to an engine, not to this repository.**

### G3 — Evidence before intelligence

The open tier's product is a complete, inspectable evidence chain:
audit events with governed call ids, per-generation archives, lineage
pins, the pinned-by reverse index, dependency edges. Anything an
engine tier concludes must be verifiable from open-tier evidence
alone (conformance kit, golden diff, run_rules). Trust comes from
evidence; evidence formats are open.

### G4 — Contracts are the stable anchor

`ordigovernance-api` is zero-dependency: protocols, pure data shapes,
policy vocabulary. Everything else — open runtime, bridges, engine
implementations — depends inward on it and never on each other. The
layering is machine-enforced (`scripts/check_import_boundary.py`),
not convention.

### G5 — Bridges are format translation only

A bridge adapts one framework's message/tool shapes to the tracked
atom protocols. It carries no governance, no orchestration semantics,
no business vocabulary. Acceptance criteria for any bridge:

- total size stays small (the langgraph reference is ~300 lines);
- never imports `examples` or another bridge;
- its contract suite proves every invocation crossing the shell
  boundary lands as a governed call with naming-discipline identity.

When a bridge feels too thin to express something, the fix belongs
in the component layer (a protocol parameter), never in bridge bulk.

### G6 — Vocabulary neutrality (inherited T6)

The component layer never invents business vocabulary: event types,
resource names, statuses-as-success, task-id shapes, tool names and
client names are all caller-declared. Viewer UI components take
token-attribution regexes, id prefixes and label formatters as
injected parameters.

### G7 — Deterministic verification without infrastructure

Every acceptance path runs on in-memory fixtures: deterministic mock
tools, scripted LLM clients, an in-memory hot path mirroring the
production redis contracts. Two runs of the same narrative normalize
to identical bundles. A feature that cannot be verified this way is
not done.

### G8 — Honest degradation

Read paths degrade, never crash: corrupt registry index reads as
empty, missing archive reads as None, a partially-written ndjson tail
is skipped, an absent audit reader yields drift=None. Write paths
fail loudly. Fakes and doubles mirror the FULL contract surface of
what they replace (pitfalls §1, §13.10).

## 3. Invariants (changes require review)

1. `ordigovernance-api` imports nothing outside the stdlib.
2. Packaged code never imports `examples` or any closed-tier
   namespace (`orditect_components`, `ordienterprise`).
3. The default (no engine injected) is bit-identical to mainline
   behavior: passthrough resolver, always-execute memoize, drift=None.
4. call_id naming discipline: `{purpose}-{task_id}-{eid}[-seq]` with
   the documented seq bands (0-9 business / 90 archive save /
   91[-hash] archive loads / 100-999 memo / 1000+ agent loop).
5. Producer/internal call classes refuse every policy override.
6. Reopen is the only cross-generation primitive; the component layer
   never invents a second generation mechanism (framework boundary 4).
7. A generation archives itself and emits governed calls even when
   pinned: pinning replaces input acquisition, never governance.
8. Registry backfills over shared state are write-once
   (docs/pitfalls.md 12.1).
9. Hot records hold only the latest generation; all history is read
   from the cold path (archive/snapshots), never by scanning hot
   records.
10. Observation never blocks the workflow (inherited T9).

## 4. Non-goals

- **Memo reuse, policy routing, drift attribution semantics** — the
  engine tier's product; only the protocols and the passthrough
  defaults live here.
- **Orchestration intelligence** — planning, scheduling, retries with
  business semantics; the patterns package ships mechanism-shaped
  primitives (fan-out, quality gate, composition), not policies.
- **Production frontend** — the viewer is a reference dashboard over
  the cold path; product UIs are the consumer's layer.
- **Framework internals** — orditect is a dependency, not a part of
  this repository; behavioral drift in the framework is a docs signal
  (update the dependency notes), never a patch opportunity here.

## 5. Extension discipline for contributors

**Adding a bridge**: translate shapes only (G5); add a contract test
proving governed-call identity across the boundary; keep optional
framework dependencies behind extras with a clear ImportError.

**Adding a pattern**: it must be mechanism-shaped — opaque items,
business facts as callbacks, no vocabulary. If the pattern needs to
interpret item contents, the interpretation is a callback.

**Adding a runtime surface**: first check whether it is mechanism
(open tier) or semantics (engine tier, G2). If it needs engine
context, expose a protocol parameter on the assembly surface instead
of branching inside the runtime (the `mode=` / `origin_seq` /
`tool_name=` parameters on AgentContext are the reference shape).

**Adding a fake or fixture**: mirror the full surface the real object
is called through (read the framework call sites, not the docs —
pitfalls §1). A fake that is one method short fails three frames
away from the cause.

**Changing evidence formats**: run the conformance kit and the
acceptance selfcheck; format drift fails CI on BOTH tiers by design.

## 6. Definition of done (any change)

1. `./run_tests.sh` green (all suites + acceptance selfcheck).
2. `python scripts/check_import_boundary.py` green.
3. New behavior locked by a test that fails without the change.
4. Any lesson learned the hard way appended to docs/pitfalls.md
   (numbered, with the locking test named).
5. Public contracts (api package) unchanged, or the change is
   additive and backwards compatible — engines build against them.
6. When the orditect framework version changes, the hot-path fixtures
   are re-verified against the real adapters (the fixture parity
   check): the same semantic assertions run over the in-memory
   fixtures and over redis before the upgrade lands. Fixture drift is
   a bug class (docs/pitfalls.md 14.8), not a test convenience.

## 7. License boundary

Engine packages in this repository are SUL-1.0. Ecosystem bridges
(langgraph, deepagents) are MIT when published from their own
repositories; the in-repo copies follow the repository license until
they move. Contributions must respect the boundary: bridge code must
never require an engine-tier import.