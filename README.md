# orditect-governance

Open governance components over the [orditect](https://github.com/orditect/orditect)
framework: protocols, mechanisms, bridges and viewers that make any
agentic workflow **auditable, replayable and governable** — regardless
of which orchestration framework drives it.

Two governance planes:

- **call level** — governed calls: semaphores, budgets, call_id
  idempotency, audit events, content pointer-ization;
- **task level** — governed agents/tasks: state machine, execution
  generations, lineage pins, dependencies, HITL, snapshot evidence.

The stack is **mechanism-direct**: the open runtime enforces every
call and every generation without carrying engine intelligence. Memo
reuse across generations, replay policy routing and drift attribution
plug in behind the api protocols at assembly time — agent and bridge
code never changes across tiers.

## Packages

PEP 420 namespace `ordigovernance.*`, independently versioned
distributions:

| package | contents |
|---|---|
| `ordigovernance` | meta package: `pip install ordigovernance[all]` pulls the full stack |
| `ordigovernance-api` | zero-dependency contracts: protocols, data shapes, policy vocabulary |
| `ordigovernance-runtime` | mechanism-direct runtime: governed agents/tools, archive, lifecycle, patterns, streams, replay mechanics |
| `ordigovernance-testing` | golden trace normalization, conformance kit, in-memory hot-path fixtures, deterministic mocks |
| `ordigovernance-viewer` | cold-path FastAPI routers + dashboard UI |
| `ordigovernance-bridges-*` | thin format-translation shells (direct / langgraph / deepagents) |

Naming: the repository is **orditect-governance**, the namespace is
`ordigovernance`, distributions are named `ordigovernance-*`.

## Architecture

```
your business code (any orchestration framework)
        │
        ▼  bridges: format translation only (≤300 lines, no governance)
ordigovernance-api ............ zero-dependency contracts
        │
        ▼  runtime: mechanism-direct enforcement
ordigovernance-runtime ........ governed agents/tools, archive,
        │                       lifecycle, patterns, replay mechanics
        ▼  engine plug-ins (behind api protocols)
memo layer · policy resolver · drift engine
        │
        ▼
orditect framework (hot path: executor, governor, budget, storage)
```

Layering rules are machine-enforced (`scripts/check_import_boundary.py`):

- `ordigovernance-api` imports nothing outside the stdlib;
- `ordigovernance-testing` imports stdlib + `ordigovernance.*` +
  `orditect.*` only;
- no packaged code imports `examples` or any closed-tier namespace.

## Framework dependency

The `orditect-*` framework packages are currently installed from a
source checkout, not from PyPI:

    # sibling checkout of the orditect repository
    pip install -e ../orditect/packages/protocol
    pip install -e ../orditect/packages/core
    pip install -e ../orditect/packages/flow
    pip install -e ../orditect/packages/stream
    pip install -e ../orditect/packages/adapter-memory
    pip install -e ../orditect/packages/adapter-local
    pip install -e ../orditect/packages/adapter-ui
    pip install -e ../orditect/packages/bridge-openai

## Install

    pip install -e ./packages/ordigovernance-api
    pip install -e ./packages/ordigovernance-runtime
    pip install -e ./packages/ordigovernance-testing
    pip install -e "./packages/ordigovernance-viewer[quickstart]"
    pip install -e ./packages/ordigovernance-bridges-langgraph
    pip install -e ./packages/ordigovernance          # meta package

For IDE-friendly editable installs (PyCharm/VSCode indexing), append
`--config-settings editable_mode=compat`.

## Quickstart

Thirty seconds to a full governed run with a live viewer — no redis,
no LLM keys, no external services. The world is deterministic (mock
tool handlers), the model is scripted, and every call still flows
through the real governed plane: semaphore, budget, audit,
generations, archive and pins.

    python -m ordigovernance.viewer.examples.quickstart
    # then open the printed URL

Watch the generations panel: the demo reopens one researcher into a
second generation, and the audit stream charges its world read twice.
That repeated spend is exactly what an engine tier's memo reuse
eliminates — the plug-in point, visible in the open tier.

## The acceptance ground

`examples/acceptance/` runs a complete workflow end to end on the
in-memory hot path (CI-friendly, zero infrastructure):

    root -> fan-out (3 researchers)
         -> quality gate (writer/review, scripted 62 -> 88)
         -> HITL pause/resume surface (cooperative cancel windows)
         -> one explicit reopen (a second generation)
         -> publish (archive + pins)

    python -m examples.acceptance.app        # one full run
    python -m examples.acceptance.selfcheck  # determinism + conformance

The self-check runs the workflow twice and asserts the two trace
bundles are structurally identical and pass the producer conformance
profile.

## Testing

    ./run_tests.sh            # all suites + the import boundary gate
    ./run_tests.sh runtime    # substring filter on suite names

Suites: runtime (assembly contracts, patterns, orchestration,
lifecycle, streams, replay mechanics with a spy drift engine),
viewer (trace endpoints, quickstart smoke), testing (golden,
conformance), bridges (format translation).

## Engine plug-in points

The open runtime ships passthrough defaults; engines implement the
api protocols and are injected at assembly time:

- `MemoLayerProtocol` — cross-generation memo reuse
  (`GovernedAgent(memo_layer_factory=...)`);
- `PolicyResolverProtocol` — replay policy routing
  (`GovernedAgent(resolver_factory=...)`);
- drift engine — attribution over replay evidence
  (`ReplayDriver(drift_engine=...)`; `None` degrades reports to
  `drift=None`).

The runtime's own suite locks the assembly contract with spies; an
engine tier's suite locks the semantics of real implementations.
Together they cover the full chain without either side importing the
other.

## Replay mechanics

`ReplayDriver` reruns nodes or DAG intervals over pinned inputs:
replay nodes are submitted under local ids (never touching mainline
hot records), reruns go through the same reopen path as HITL, and
every generation archives and audits exactly like a mainline
generation. Baselines for drift comparison come from the run's own
snapshot bundle, never from shared hot records.

## Documentation

- [docs/pitfalls.md](docs/pitfalls.md) — hard-won lessons from
  bringing up the real-executor acceptance ground (fake contract
  surfaces, resource/semaphore name alignment, retry_scope
  discipline, hang diagnosis). Read before extending the runtime,
  the testing fixtures, or the examples.

## License

Engine packages are licensed under the Sustainable Use License (see
LICENSE). Bridges published from their own repositories are MIT.