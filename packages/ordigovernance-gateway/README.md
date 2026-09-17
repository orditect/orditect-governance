# ordigovernance-gateway

HTTP execution front of the ordigovernance hot path — the WRITE path
for remote orchestrators (n8n). The viewer routers remain the READ
path (evidence). HTTP terminates at governed-call / task-action
granularity; redis primitives (semaphores, budget, hot records) never
leave this process.

Design and construction plan: ../../docs/n8n-bridge-design.md

## Run

    pip install -e "./packages/ordigovernance-gateway[memory]"
    export GATEWAY_AUTH_TOKEN=...
    export GATEWAY_REGISTRY_MODULE=yourpkg.registry:build_registry
    uvicorn ordigovernance.gateway.app:build_app --factory --port 8180

Configuration is env-driven (see `ordigovernance.gateway.config`); a
repo-local `.env` is honored and reported on startup (source, parser,
key count). Existing process environment always wins over `.env`.

Key settings: `GATEWAY_REDIS_URL` (unset = in-memory hot path, the
`memory` extra), `GATEWAY_SEMAPHORES` / `GATEWAY_MODEL_CLIENTS` (JSON
objects), `OPENAI_BASE_URL` / `OPENAI_API_KEY` (`OPENAI_API_BASE`
accepted as an alias), `GATEWAY_TRACE_ROOT`, `GATEWAY_BUDGET_MAX_UNITS`,
`GATEWAY_REGISTRY_MODULE`.

## Deployment registry (D6)

The gateway ships protocols only; impls/tools/composites are injected
at deployment time through two channels, merged at boot:

1. entry points groups `ordigovernance.gateway.tools` / `.impls` /
   `.composites` (each entry point loads to a callable returning a
   contribution dict);
2. `GATEWAY_REGISTRY_MODULE=module:callable` (overrides entry-point
   contributions on name conflicts).

Dynamic loading bypasses the static import-boundary gate, so every
loaded factory is re-checked against the forbidden closed-tier
namespaces at boot; a hit refuses the boot and lists the offender.

Factory signatures:

    ToolHandlerFactory: (session) -> async handler callable
    ImplFactory:        (params: dict, surfaces) -> AgentProtocol
    CompositeFactory:   (params: dict, session) -> coroutine (outcome dict)

The gateway assembles the GovernedAgent itself (per-task tool set,
shared llm registry, archive backend, memo scope = the run's budget
scope); impl factories return only the business impl. `surfaces`
carries `storage` (hot records, for reading upstream results),
`run_id` and `session`.

Reference implementation: `examples/gateway_n8n/registry.py`.

## Authentication

All endpoints except `/healthz` require
`Authorization: Bearer <GATEWAY_AUTH_TOKEN>` (single execute-scope
token, V1).

## API

### Call plane (ambient or run-scoped)

    POST /governed/llm-chat     {run_id?, task_id?, client, purpose?,
                                 messages[], kwargs?}
                              -> {status, call_id, response, usage?}
    POST /governed/tool-call    {run_id?, task_id?, tool, inputs{}, reuse?}
                              -> {status, call_id, result, origin}

No `run_id` routes to the **ambient run** (opened at boot, never
finished, not counted as the single active run). Given `task_id`, the
hot record must exist (404 otherwise); omitted, an ephemeral identity
is minted. call ids always follow the naming discipline; seq slots
are per (task_id, purpose) starting above the agent band. `origin`
is `"executed"` on the open tier (memo reuse is an engine concern).
Errors: unknown vocabulary -> 422 listing valid names; unknown task
-> 404; budget/quota denial -> 409; step timeout -> 504; reserved
payload keys (`params`, `call_id`, `seq`, ...) -> 422 with a rename
instruction.

### Task plane (active run only)

    POST /runs                     {run_id?, budget_max_units?} -> 201
    GET  /runs                     registry, newest first
    GET  /runs/{id}                entry (+ live task states while active)
    POST /runs/{id}/tasks          TaskDescriptor -> 201 {task_id, accepted}
    GET  /runs/{id}/tasks/{tid}    {status, execution_id,
                                    previous_execution_ids[], result?}
    POST /runs/{id}/finish         teardown + registry finish
    GET  /runs/{id}/vocabulary     {impls, tools, composites}

TaskDescriptor: `{task_id, impl, params{}, upstream[], parent_task_id?,
tools?[]}`. `upstream` writes dependency edges (child = this task,
parent = u) — evidence/graph, never scheduling; the orchestrator owns
ordering. Duplicate task_id within a run -> 409 (a rerun goes through
HITL retry); the duplicate guard is run-scoped and task reads resolve
ownership through the session, never through the shared hot path
(docs/pitfalls.md 16.6/16.7).
finish -> 409 listing non-terminal tasks; after finish the run is no
longer active and hot reads close (historical evidence belongs to the
viewer cold path). The vocabulary endpoint also answers on
`/runs/ambient/vocabulary` for design-time queries.

### HITL plane (active run only)

    POST /runs/{id}/hitl/pause     {task_id}   -> acceptance receipt
    POST /runs/{id}/hitl/resume    {root_id?}  -> acceptance receipt
    POST /runs/{id}/hitl/retry     {task_id}   -> direct reopen + resubmit
    GET  /runs/{id}/hitl/receipt/{action_id}   -> execution receipt
                                                  (404 while pending)

Dual receipt discipline: mutating calls return the ACCEPTANCE
receipt; poll the receipt endpoint for the EXECUTION receipt. Retry:
terminal-only (409 otherwise), run root rejected (422), orphan
guard rejects when declared descendants are ACTIVE (409), task
rebuilt from the registered descriptor. Retry is synchronous, so its
`retry-direct-*` receipt answers immediately at the receipt endpoint
(pitfalls 16.8). All endpoints 404 once the run ends.

### Composites (drive-level background drivers)

    POST /runs/{id}/composites             {name, params{}} -> 202
    GET  /runs/{id}/composites/{cid}       {status, children[], outcome}

A composite is a registered `(params, session) -> coroutine` driver
executing as a background asyncio task; child tasks attribute to it
automatically (contextvar), evidence closes through the child tasks.

### Health

    GET /healthz -> {status, active_run, ambient}

## Boundary

Redis primitives never leave this process. Historical evidence reads
belong to the viewer (point it at the same `GATEWAY_TRACE_ROOT`; see
`examples/gateway_n8n/viewer_app.py`).