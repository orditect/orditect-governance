# Engine integration guide

The open tier ships mechanism-direct defaults; engines with richer
semantics plug in behind the api protocols at assembly time. This
document is the closed tier's contract sheet AND the open tier's
acceptance checklist: every injection point must have all four items
before an engine tier is built against it.

Per injection point, the four required pieces:

1. **api protocol** — the runtime_checkable protocol anchor;
2. **injection point** — the assembly parameter that receives the engine;
3. **passthrough baseline** — what happens with no engine (the default
   is part of the public contract, never an accident);
4. **conformance / test surface** — where the contract is locked.

## Injection points

| engine concern | api protocol | injection point | passthrough baseline | contract surface |
|---|---|---|---|---|
| memo layer (cross-generation reuse) | `MemoLayerProtocol` | `GovernedAgent(memo_layer_factory=...)` | `ctx.memoize` always executes; origins record `executed` | runtime `test_engine_plugin.py`, `test_acceptance_spy.py`; testing `run_engine_memo_profile` (engine self-check) |
| replay policy routing | `PolicyResolverProtocol` | `GovernedAgent(resolver_factory=...)` | `PassthroughResolver`: declared reuse in force, external fires, stub unreachable | runtime `test_passthrough_resolver.py` |
| drift attribution | `DriftEngineProtocol` | `ReplayDriver(drift_engine=...)` | reports carry `drift=None` | runtime `test_replay_driver.py` (spy engine), `test_replay_range_baselines.py` |
| pin-vs-archive reconciliation | `ReconcileFnProtocol` (returns `ReconcileReportShape`) | `build_trace_router(reconcile_fn=...)` | `/validate` returns run_rules findings only, zero DR-PIN-* rows | viewer `test_trace_validate_partial.py` |
| tracked LLM atom | `TrackedLLMProtocol` | bridge assembly (e.g. `build_react_agent(tracked_llm, ...)`) | `PassthroughTrackedLLM`: governed, non-memoized | runtime `test_passthrough_atoms.py`, bridges contract suites |
| tracked tool set | `TrackedToolSetProtocol` | bridge assembly (e.g. `as_langchain_tools(tracked, ...)`) | `PassthroughTrackedToolSet`: governed, non-memoized; origins record `executed` | runtime `test_passthrough_atoms.py` |
| stub audit record surface | structural: `record_stub_decision(name, *, call_id, inputs, policy_table)` | the governed tool registry backing `ctx.tools` | no-op when the surface is absent; origins remain the fallback evidence | runtime `test_stub_audit.py` |

## Assembly invariants (both tiers)

- A generation's engine instances are built ONCE per generation from
  the assembly factories, with the generation facts (task_id, eid,
  previous_status, scope) and the policy channels (policy table,
  legacy override) as the only inputs.
- Engine selection happens at assembly time only. Business code,
  bridges, and impls never branch on engine presence.
- An absent engine never degrades evidence: archives, audit rows and
  generations are produced identically with or without engines.

## Public read surface (what engine components may consume)

Engine components read the generation's facts through AgentContext's
public surface only — never its private fields. The surface:

| member | meaning |
|---|---|
| `ctx.meta` | generation identity (task_id, eid, previous_eids, previous_status) |
| `ctx.memo_scope` | the run-scoped memo namespace |
| `ctx.memo_backend` | the backend backing memo/archive (None ungoverned) |
| `ctx.tools` | the A-class governed tool registry |
| `ctx.llm_registry` | a copy of the B-class client registry |
| `ctx.policy_resolver` | the generation's policy resolver |
| `ctx.has_memo_layer` | whether a memo engine is injected |
| `ctx.require_memo_layer(feature)` | the memo layer, or a RuntimeError naming the injection point |

A component that needs the memo layer MUST go through
`require_memo_layer`: the passthrough tier fails with a message
naming the assembly parameter, never with an AttributeError on a
private field.

## Versioning discipline

Released protocols are only ever extended additively. A semantics
change ships as a new protocol (e.g. `PolicyResolverV2Protocol`),
never as a silent edit — engine implementations and third-party
bridges build against these shapes.

## Engine integration preflight

Before writing an engine tier, run the open tier's self-checks
against the target injection point:

1. runtime spy suites for the factory arguments and the delegation
   shapes (`test_engine_plugin.py`, `test_replay_driver.py`,
   `test_acceptance_spy.py`);
2. `run_engine_memo_profile(memo_factory)` from
   `ordigovernance.testing.conformance` for the memo engine's
   behavioral contract (fabrication is forbidden; reuse is not
   required);
3. the public read surface inventory in this document plus
   `docs/passthrough-baseline.md` as the parity reference.

A tier that passes all three builds against the same contracts the
open tier enforces in CI.