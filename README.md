# ordigovernance

Open governance components over the orditect framework: protocols,
mechanisms, bridges and the viewer that make any agentic workflow
auditable and replayable.

Packages (PEP 420 namespace `ordigovernance.*`, independently versioned):

| package | contents |
|---|---|
| `ordigovernance-api` | zero-dependency contracts: protocols, data shapes, policy vocabulary |
| `ordigovernance-runtime` | mechanism-direct runtime: governed agents/tools, archive, lifecycle, patterns, streams |
| `ordigovernance-testing` | golden trace normalization, conformance kit, deterministic mocks |
| `ordigovernance-viewer` | cold-path FastAPI routers + dashboard UI |
| `ordigovernance-bridges-*` | thin format-translation shells (direct / langgraph / deepagents) |

## Quickstart

    pip install "ordigovernance-runtime[viewer]"
    python -m ordigovernance.examples.quickstart
    # open the printed URL: DAG, generations, audit receipts, water levels