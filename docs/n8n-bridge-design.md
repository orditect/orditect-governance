# n8n Bridge — Design and Decisions

  > The node-side contract summary and HTTP-visible pitfalls live in
  > the n8n-nodes-ordigovernance repository (docs/gateway-contract.md,
  > docs/gateway-pitfalls.md).

This document is the design record of the n8n bridge: the frozen
decisions, the architecture they imply, and the lessons verified
against real endpoints. Construction progress does not belong here;
operational lessons are numbered in docs/pitfalls.md, and the manual
release gate lives in the n8n repository (RELEASE-SMOKE.md).

Deliverables:

| deliverable | location | license |
|---|---|---|
| `ordigovernance-gateway` | `packages/ordigovernance-gateway` | SUL-1.0 |
| `n8n-nodes-ordigovernance` | own repository | MIT |
| `examples/gateway_n8n/` | this repository, `examples/` | SUL-1.0 |

Boundary discipline: the gateway never imports `examples` (the static
import gate covers `packages/*/src`); the reference registry is
injected at deployment time; the n8n package is a pure HTTP client
plus format-translation shells and never imports any governance or
engine package.

## 1. Frozen decisions (D1-D14)

These decisions are closed. Changing them requires design review.

| # | decision | content |
|---|---|---|
| D1 | upstream semantics | `submit_task` writes one dependency edge per `u ∈ upstream` (child = this task, parent = u, all `is_primary=True`). upstream is **evidence/graph**, never scheduling; n8n owns ordering, the gateway ships no register/notify/wait chain |
| D2 | run-less calls | One **ambient run** opens at boot (`run_id="ambient"`, never finished, torn down at shutdown); call-plane requests without `run_id` route there. It does not count as the single active run |
| D3 | vocabulary endpoint | `GET /runs/{id}/vocabulary` is a first-class API; n8n nodes query it at design time (it also answers on the ambient run) |
| D4 | derived defaults | `budget_scope` derives uniquely per run when omitted; trace layout is `trace_root/{run_id}/trace`; the registry entry carries the real scope at registration (no backfill) |
| D5 | finish semantics | Any non-terminal task at finish → 409 listing the tasks; all terminal → teardown + registry finish |
| D6 | registry loading | Two channels (entry points + config module), merged at boot; every loaded factory is re-checked against the forbidden closed-tier namespaces at boot, a hit refuses the boot and lists the offender |
| D7 | duplicate task_id | Same-run duplicate submission → 409; a rerun goes through HITL retry. **Scope**: run-scoped (descriptor registry), never the shared hot path (pitfalls 16.6) |
| D8 | call-plane identity | Existing task in the run → current eid from the hot record; task given but unknown **to the run** → 404 (pitfalls 16.7); omitted → ephemeral identity. seq is allocated per (task_id, purpose), starting above the agent band. **Ambient exemption**: run-less traffic attributes to any existing record (the D2 catch-all); 404 when no record exists |
| D9 | tool input shape | `inputs` is expanded as handler kwargs; a hit on the reserved plumbing keys → 422 with a rename instruction (same wording as the runtime atoms) |
| D10 | composite shape | Drive-level background drivers: a third registry table `composites`, `POST /runs/{id}/composites` starts one, `GET .../composites/{cid}` polls it; children attach to the run root and evidence closes through the child tasks. Supervisor-node packaging is deferred to V2 (pitfalls 4/13.1 constraints) |
| D11 | multi-item discipline | A Task node executes once per n8n item; colliding evaluated task_ids are auto-suffixed with the item index (documented behavior) |
| D12 | client assembly seam | settings exposes `make_clients`: the default assembles the real GovernedLLMClient registry, tests inject scripted clients (gateway tests never need a real endpoint) |
| D13 | memo body default | The gateway ships its own memory_read/write pair: redis-backed in redis mode, process-dict in memory mode; both overridable. Production paths never run on test fixtures |
| D14 | historical reads | The gateway serves hot reads of the ACTIVE run only; historical evidence is the viewer's job over the shared `trace_root`. No history endpoints on the gateway |
| D15 | run cancel | `POST /runs/{id}/cancel` (no body): cooperative-cancel every RUNNING task, wait for terminal settlement (settle timeout defaults to 150s -- it must exceed the slowest task window with slack, see pitfalls 18.4), then close the run as cancelled. The escape hatch for wedged runs (finish 409s on non-terminal tasks; a gateway restart kills the dispatcher, pitfalls 16.9). The node-side Cancel operation and conflict recovery route through it |
| D16 | run provenance | `StartRunRequest` carries optional `intent` / `metadata`, recorded verbatim on the registry entry (business provenance; the gateway never interprets them). Additive to D4 |
| D17 | streaming | `POST /governed/llm-chat-stream`: same body as llm-chat, SSE frames `{delta\|done\|error}`; the done frame carries `call_id` + `usage` (include_usage forced on). Serves the node shell's token-level `_astream`; 422 with guidance when the client has no `stream()` |

## 2. Architecture

```
n8n canvas (scheduling, UX, human decisions)
   │  HTTP only (bearer token)
   ▼
ordigovernance-gateway (the WRITE path)
   call plane   /governed/llm-chat, /governed/tool-call
   task plane   /runs, /runs/{id}/tasks, finish, vocabulary
   HITL plane   /runs/{id}/hitl/{pause,resume,retry,receipt}
   composites   /runs/{id}/composites[/{cid}]
   │
   ▼  one process-wide hot path (semaphores are process-global:
   │     one backend, one resource reality)
ordigovernance-runtime (governed agents/tools, lifecycle, patterns)
   │
   ▼
orditect framework (executor, governor, budget, storage)
```

Read path: the viewer routers point at the same `GATEWAY_TRACE_ROOT`
(cold path only). One gateway process = write path; the viewer =
read path (D14).

Key properties:

- **Mechanism-direct**: the gateway enforces every call and every
  generation but carries no engine intelligence (no memo reuse, no
  policy routing); origins report `"executed"`.
- **Single active run**: one user run at a time (process-local
  guard); the ambient run coexists with it (D2).
- **Session-scoped reads**: ownership resolves through the session's
  descriptor registry before the shared hot path is touched
  (pitfalls 16.6/16.7).
- **Composites are drive-level** (D10): background asyncio tasks
  inside the session, never supervisor nodes; child attribution via
  a contextvar; a composite failure lands on its own status endpoint,
  never on the run.

## 3. Node-side design notes (n8n repository)

- **TrackedChatModel** is a `BaseChatModel` shell: every invocation
  is one governed `POST /governed/llm-chat`. Bound tools /
  tool_choice / stop / extra bind kwargs forward verbatim on every
  call; the unbound model never carries tool specs. Assistant
  tool_calls survive the round-trip in whichever shape the middleware
  left them (`additional_kwargs.tool_calls` fallback included).
  `bindTools` exists and goes through `withConfig` — n8n's Tools
  Agent probes `typeof model.bindTools === 'function'` before
  accepting a chat model.
- **Approval node** maps mechanism primitives to approval semantics:
  pause suspends, resume/retry is the human "approve" (a new
  generation on the hot record), finishing the run is "reject"
  (the hot record closes, D14). The gateway ships no approval
  resource — trigger policy belongs to the canvas (G2/G6 spirit).
- **Dual receipt discipline**: mutating HITL calls return the
  acceptance receipt; the execution receipt is polled
  (404 = pending). Direct (non-sink) retry actions record their
  receipt synchronously (pitfalls 16.8).
- **Terminal is not success**: task and composite nodes treat any
  non-succeeded terminal status as a node error, so the canvas never
  reports a dead workflow as green.
- **Id hygiene**: every string id parameter is trimmed (expression
  fields treat surrounding whitespace as literal text), and task ids
  must be unique per execution (`{{ $execution.id }}` suffix) — the
  duplicate guard is run-scoped, but cross-run id reuse muddies
  evidence attribution.

## 4. Lessons (bridge-specific; general ones live in docs/pitfalls.md)

1. **Peer-dependency discipline is existential.** `@langchain/core`
   must resolve to THE SAME instance n8n loads, or
   `instanceof BaseChatModel` silently fails and the AI Agent node
   rejects the model. Verified at M1; pinned as a peerDependency
   with an explicit range; `npm ls @langchain/core` must show one
   copy.
2. **Probe the bare client contract before any react loop.** A
   missing tools forward fails silently (the model answers in plain
   text, no error). Layered probing (bare client → agent loop)
   attributes breakage to exactly one component (pitfalls 14.1).
3. **Field-verified at M2**: a gateway with no registry loaded
   answers `422 ... registered: []` one node late — always boot via
   `scripts/dev-gateway.sh`; tool arguments travel under `inputs`
   (handler kwargs); the poll endpoint is run-scoped.
4. **Field-verified at M3**: HITL actions on a dead run are accepted
   into a dead dispatcher and never execute (pitfalls 16.9) — never
   restart the gateway mid-run; receipts plus the hot record's
   `previous_execution_ids` are the only truth.
5. **Field-verified at M4**: a failed task was reported as a green
   node (terminal ≠ success — fixed and test-locked); fan-in through
   a Merge node double-dispatched the downstream node (append mode
   fires per arriving input) — the working topology wires the fan-in
   node behind ONE upstream branch and references the others by name.
6. **Field-verified at M5**: the quality gate converged through real
   model scores [42, 92] with two sink-driven generations per child —
   composite children register their descriptors BEFORE the wrapped
   pattern submits them (pitfalls 16.4), keeping one construction
   source for first submit and recovery alike.
7. **Contract drift is silent by design of the schema layer.** The
   submit body's impl payload field is `params`; pydantic ignores
   unknown fields, so sending `input` fails silently as impl defaults
   (caught in the wild by a `topic: "general"` result). Locked
   node-side by a regression test; the openapi.json schema is the
   only contract truth.
8. **The acceptance ≠ execution gap needs client-side patience.**
   `awaitDecision` returns when the new generation APPEARS; the
   generation may still be executing, so an immediate finish hits
   the D5 409. Clients either wait for the terminal record or scope
   their actions accordingly (RELEASE-SMOKE documents the verified
   choreography).
9. **Field-level contract drift ships silently.** The n8n Run/Tool nodes once sent `client`/`purpose`/`metadata` bodies the gateway schema never declared; pydantic dropped them, three node parameters were decorative and the budget cap was unreachable -- with every node test green, because the mocks encoded the same wrong contract. Fix on both sides: schema-alignment regression locks in the node suite, plus a gateway-side wire-contract mirror (`test_node_wire_contract.py`) that extracts the request schemas' field sets from the app's own openapi, so a schema edit that would drop node fields fails the gateway build first.
10. **SourceChunk objects must be field-extracted, never repr'd.**
    The real governed client streams orditect SourceChunk objects,
    not dicts; the SSE endpoint's first chunk parser hit its
    non-dict fallback and shipped the object REPR in every text
    field (discovered only in live acceptance -- the scripted test
    client yields dicts). The parser now probes text/thinking
    attributes (duck-typed), maps thinking to the reasoning channel,
    and the TERMINAL marker chunk (both fields None, finish=True)
    emits no frame at all. Two meta-lessons: scripted test clients
    should mirror the real client's chunk TYPES, not just their dict
    shapes; and every fallback branch in a wire serializer deserves
    a "what does this do to the consumer" second look (a repr in a
    text field is valid JSON and fails no test).

## 5. Verification assets

- Gateway suite: `packages/ordigovernance-gateway/tests` (routes,
  sessions, composites, registry self-check, demo-registry E2E).
- Node suite: `n8n-nodes-ordigovernance/test` (contract tests over a
  mocked gateway, including the dual-receipt and terminal-status
  locks).
- Workflow JSONs: `n8n-nodes-ordigovernance/workflows/` (m3 hitl,
  m4 narrative fan-in, m5 quality gate) — the regression assets for
  the manual release gate.
- Release gate: `n8n-nodes-ordigovernance/RELEASE-SMOKE.md` —
  manual, real n8n + real gateway + real endpoint, ~15 minutes,
  mandatory before publishing. CI covers contracts; the smoke covers
  real runtime behavior mocks cannot see.

## 6. Deferred to V2

- Supervisor-node packaging of composites (pitfalls 4/13.1
  constraints; D10's drive-level shape stands for V1).
- n8n `putExecutionToWait` for long tasks (V1: submit-and-poll with
  configurable timeouts).
- Approval as an explicit resource (would require mechanism-layer
  vocabulary; currently mapped through pause/resume evidence).
- Scoped/multi-token auth (V1: single bearer token, execute scope).