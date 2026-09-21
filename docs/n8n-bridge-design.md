# n8n Bridge — Consumer Record

  > The node-side contract summary and HTTP-visible pitfalls live in
  > the n8n-nodes-ordigovernance repository (docs/gateway-contract.md,
  > docs/gateway-pitfalls.md).

This document is the **n8n-consumer companion** to the gateway: the
node-package design notes, the lessons verified against real n8n
endpoints, and the verification assets. The normative gateway design
record — the frozen decisions D1–D18 and the architecture — lives in
[docs/gateway-design.md](gateway-design.md); the gateway itself is
orchestrator-neutral (n8n is its first-class consumer, not its
owner). Construction progress does not belong here; operational
lessons are numbered in docs/pitfalls.md, and the manual release gate
lives in the n8n repository (RELEASE-SMOKE.md).

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




## 1. Node-side design notes (n8n repository)

- **No custom chat-model node** (D18 consequence): the LLM leg of the
  AI Agent runs on n8n's BUILT-IN OpenAI Chat Model node pointed at
  the gateway's `/v1` surface (credential: Base URL
  `http://<gateway>/v1`, API Key = `GATEWAY_AUTH_TOKEN`, optional
  custom header `X-Governance-Run-Id: @active`). This keeps the node
  package free of `@langchain/core` (peer-instance alignment is an
  n8n-internal concern the built-in node already solves) and free of
  the still-preview `@n8n/ai-node-sdk`.
- **Tool legs**: parameterized tools use the built-in Call n8n
  Workflow Tool wrapping a sub-workflow whose first node is the
  Ordigovernance Tool community node (attribution via node
  expressions); simple tools may use the built-in HTTP Request Tool
  against `/governed/tool-call` (body carries `tool` / `inputs` /
  `run_id`; AI parameter injection uses the tool-node placeholder
  mechanism -- hand-written `$fromAI(...)` is not supported there).
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

## 2. Lessons (bridge-specific; general ones live in docs/pitfalls.md)

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

## 3. Verification assets

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

## 4. Deferred to V2

- Supervisor-node packaging of composites (pitfalls 4/13.1
  constraints; D10's drive-level shape stands for V1).
- n8n `putExecutionToWait` for long tasks (V1: submit-and-poll with
  configurable timeouts).
- Approval as an explicit resource (would require mechanism-layer
  vocabulary; currently mapped through pause/resume evidence).
- Scoped/multi-token auth (V1: single bearer token, execute scope).