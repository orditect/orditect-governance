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

The 8 governance distributions share one PEP 420 namespace
(`ordigovernance.*`). Setuptools' default editable mode resolves it
through an import hook, which is fragile for namespace subpackages —
and mixing strict and compat editable installs across these packages
silently makes some subpackages unimportable. Install ALL governance
packages with the compat mode (plain .pth paths) from the start:

    pip install -e "./packages/ordigovernance-api[dev]" --config-settings editable_mode=compat
    pip install -e "./packages/ordigovernance-runtime[dev]" --config-settings editable_mode=compat
    pip install -e "./packages/ordigovernance-testing[dev]" --config-settings editable_mode=compat
    pip install -e "./packages/ordigovernance-viewer[dev,quickstart]" --config-settings editable_mode=compat
    pip install -e "./packages/ordigovernance-bridges-langgraph[dev]" --config-settings editable_mode=compat
    pip install -e "./packages/ordigovernance-bridges-direct[dev]" --config-settings editable_mode=compat
    pip install -e "./packages/ordigovernance-bridges-deepagents[dev]" --config-settings editable_mode=compat
    pip install -e "./packages/ordigovernance[all,dev]" --config-settings editable_mode=compat

Compat mode also fixes PyCharm/VSCode indexing out of the box.

**Editable-mode discipline**: if you ever reinstall one governance
package, reinstall ALL of them with the SAME mode — never mix strict
and compat across the `ordigovernance.*` namespace. The safest reset
is a full uninstall plus the block above:

    pip uninstall -y ordigovernance ordigovernance-api \
        ordigovernance-runtime ordigovernance-testing \
        ordigovernance-viewer ordigovernance-bridges-langgraph \
        ordigovernance-bridges-direct ordigovernance-bridges-deepagents

The `orditect-*` framework packages (see Framework dependency) are
installed with plain `pip install -e` and do NOT need the compat
flag; the two namespaces are independent.

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

### Verifying against a real endpoint

The suites are hermetic: mock tool handlers, scripted models,
in-memory hot path. To verify the bridges against a real
OpenAI-compatible endpoint, use the layered probe:

    cp .env.example .env
    # fill OPENAI_BASE_URL / OPENAI_API_KEY
    # (OPENAI_API_BASE is accepted as an alias)

    PROBE_LAYER=1   python scripts/probe_bridge_env.py
    # bare GovernedLLMClient: tools forwarding, usage billing

    PROBE_LAYER=all python scripts/probe_bridge_env.py
    # + layer 2: langgraph react loop through GovernedAgent
    # + layer 3: deepagents agent (requires the deepagents extra)

Layers 2/3 drive a real react loop: the audit stream must show the
governed tool call id (`search-<task>-<eid>-1001`), per-call token
usage on every llm_call event, and the archive save
(`memsave-...-90`). Deepagents note: expect llm_call token counts
roughly an order of magnitude above langgraph's — the middleware
system prompt is billed too, and the audit stream is exactly where
that cost becomes visible.

Framework compatibility: langgraph V1+ (factory at
langchain.agents.create_agent) and legacy langgraph are both
supported; deepagents >= 0.7 is required (the system_prompt era).

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

## Tier map (orditect-components -> tiers)

The legacy monorepo `orditect-components` was split into this open
stack plus a closed engine tier. Where each old surface landed:

| old surface | open tier | engine tier |
|---|---|---|
| naming discipline, store/task/llm protocols, policy vocabulary | `ordigovernance-api` | built on it |
| governed agents/tools, archive, pins, lifecycle, patterns, streams | `ordigovernance-runtime` | built on it |
| replay mechanics (submit/reopen/baselines/evidence assembly) | `ordigovernance-runtime` | — |
| memo layer (cross-generation reuse), PolicyResolver, drift attribution | protocols only (`MemoLayerProtocol`, `PolicyResolverProtocol`) | implementations |
| `@governed_interval` / `IntervalBinder` (decorator-first assembly) | — | ✓ |
| nested intervals (multi-agent, namespace isolation) | — | ✓ |
| golden / conformance kit, deterministic mocks, hot-path fixtures | `ordigovernance-testing` | shared (both tiers run it) |
| viewer routers + dashboard | `ordigovernance-viewer` | — |

The last two rows are deliberate DX differentiators, not engine
intelligence; everything the open tier ships is functionally complete
for governed runs, replay mechanics and evidence inspection.

## Replay mechanics

`ReplayDriver` reruns nodes or DAG intervals over pinned inputs:
replay nodes are submitted under local ids (never touching mainline
hot records), reruns go through the same reopen path as HITL, and
every generation archives and audits exactly like a mainline
generation. Baselines for drift comparison come from the run's own
snapshot bundle, never from shared hot records.

## Documentation

- [docs/design-goals.md](docs/design-goals.md) — design goals,
  invariants and extension discipline for external contributors.
- [docs/pitfalls.md](docs/pitfalls.md) — hard-won lessons from
  bringing up the real-executor acceptance ground (fake contract
  surfaces, resource/semaphore name alignment, retry_scope
  discipline, hang diagnosis). Read before extending the runtime,
  the testing fixtures, or the examples.

## License

Engine packages (`ordigovernance-api`, `ordigovernance-runtime`,
`ordigovernance-testing`, `ordigovernance-viewer`,
`ordigovernance-bridges-direct`, the meta package `ordigovernance`)
are licensed under the Sustainable Use License 1.0 — see [LICENSE](LICENSE).
The SUL permits any use except offering the Software as a competing
hosted or managed service.

Ecosystem bridges published from their own repositories
(`ordigovernance-bridges-langgraph`,
`ordigovernance-bridges-deepagents`) are MIT-licensed there; the
skeletons frozen in this repository follow the repository license
until they move out.

### Trademark

"orditect" and "ordigovernance" are trademarks of the orditect
project (github.com/orditect). This license does not grant any right
to use those names, logos, or marks. Third parties may refer to the
projects nominatively but must not use the marks in a way that
suggests endorsement or affiliation.
