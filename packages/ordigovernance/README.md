# ordigovernance

Open governance components over the orditect framework: protocols,
mechanisms, bridges and the viewer that make any agentic workflow
auditable and replayable.

Naming: this repository is **orditect-graph**; the PEP 420 namespace
is `ordigovernance.*`; distributions are named `ordigovernance-*`.

Packages (PEP 420 namespace `ordigovernance.*`, independently versioned):

| package | contents |
|---|---|
| `ordigovernance` | meta package: `pip install ordigovernance[all]` pulls everything below |
| `ordigovernance-api` | zero-dependency contracts: protocols, data shapes, policy vocabulary |
| `ordigovernance-runtime` | mechanism-direct runtime: governed agents/tools, archive, lifecycle, patterns, streams, replay mechanics |
| `ordigovernance-testing` | golden trace normalization, conformance kit, deterministic mocks |
| `ordigovernance-viewer` | cold-path FastAPI routers + dashboard UI |
| `ordigovernance-bridges-*` | thin format-translation shells (direct / langgraph / deepagents) |

## Framework dependency

The `orditect-*` framework packages are currently injected from a
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

## Develop

    pip install -e ./packages/ordigovernance-api
    pip install -e ./packages/ordigovernance-runtime
    pip install -e ./packages/ordigovernance-testing
    pip install -e ./packages/ordigovernance-viewer[quickstart]
    ./run_tests.sh

## Quickstart

    pip install "ordigovernance-viewer[quickstart]"
    python -m ordigovernance.viewer.examples.quickstart
    # open the printed URL: DAG, generations, audit receipts, water levels

## License

Engine packages are licensed under the Sustainable Use License (see
LICENSE). Bridges published from separate repositories are MIT.