# Gateway — Design and Decisions

The gateway is the **HTTP execution front (write path)** of the
ordigovernance hot path, for remote orchestrators. n8n is the
first-class consumer (via
[n8n-nodes-ordigovernance](https://github.com/orditect/n8n-nodes-ordigovernance)),
but every surface serves any HTTP client identically: scripts, CI,
other node ecosystems, and — through the OpenAI-compatible `/v1`
surface (D18) — any OpenAI-compatible client. The viewer routers
remain the READ path (evidence).

The n8n-consumer companion record (node-package notes, n8n field
lessons, verification assets) lives in
[n8n-bridge-design.md](n8n-bridge-design.md).

This document states the frozen decisions, the architecture they
imply, and the repository's integration growth model. Construction
progress does not belong here; operational lessons are numbered in
docs/pitfalls.md.

## 1. Frozen decisions (D1–D18)

These decisions are closed. Changing them requires design review.
(D15–D17 backfill existing, implemented behavior — cancel, run
provenance and streaming shipped before this table was renumbered.)

| # | decision | content |
|---|---|---|
| D1 | upstream semantics | `submit_task` writes one dependency edge per `u ∈ upstream` (child = this task, parent = u, all `is_primary=True`). upstream is **evidence/graph**, never scheduling; the remote orchestrator owns ordering, the gateway ships no register/notify/wait chain |
| D2 | run-less calls | One **ambient run** opens at boot (`run_id="ambient"`, never finished, torn down at shutdown); call-plane requests without `run_id` route there. It does not count as the single active run |
| D3 | vocabulary endpoint | `GET /runs/{id}/vocabulary` is a first-class API; design-time clients (the n8n nodes) query it before any user run exists (it also answers on the ambient run) |
| D4 | derived defaults | `budget_scope` derives uniquely per run when omitted; trace layout is `trace_root/{run_id}/trace`; the registry entry carries the real scope at registration (no backfill) |
| D5 | finish semantics | Any non-terminal task at finish → 409 listing the tasks; all terminal → teardown + registry finish |
| D6 | registry loading | Two channels (entry points + config module), merged at boot; every loaded factory is re-checked against the forbidden closed-tier namespaces at boot, a hit refuses the boot and lists the offender |
| D7 | duplicate task_id | Same-run duplicate submission → 409; a rerun goes through HITL retry. **Scope**: run-scoped (descriptor registry), never the shared hot path (pitfalls 16.6) |
| D8 | call-plane identity | Existing task in the run → current eid from the hot record; task given but unknown **to the run** → 404 (pitfalls 16.7); omitted → ephemeral identity (`remote-call-<hex>`). seq is allocated per (task_id, purpose), starting above the agent band (default purpose `remote-chat`; the OpenAI-compatible surface defaults to `openai-compat`). **Ambient exemption**: run-less traffic attributes to any existing record (the D2 catch-all); 404 when no record exists. *(Pre-release rename: the defaults were `n8n-chat` / `n8n-call-` before the gateway was repositioned as orchestrator-neutral; renamed before the first release, so there are no consumers to migrate.)* |
| D9 | tool input shape | `inputs` is expanded as handler kwargs; a hit on the reserved plumbing keys → 422 with a rename instruction (same wording as the runtime atoms) |
| D10 | composite shape | Drive-level background drivers: a third registry table `composites`, `POST /runs/{id}/composites` starts one, `GET .../composites/{cid}` polls it; children attach to the run root and evidence closes through the child tasks. Supervisor-node packaging is deferred to V2 (pitfalls 4/13.1 constraints) |
| D11 | multi-item discipline | A remote client may execute one submission per item; colliding evaluated task_ids are auto-suffixed with the item index (documented n8n node behavior) |
| D12 | client assembly seam | settings exposes `make_clients`: the default assembles the real GovernedLLMClient registry, tests inject scripted clients (gateway tests never need a real endpoint) |
| D13 | memo body default | The gateway ships its own memory_read/write pair: redis-backed in redis mode, process-dict in memory mode; both overridable. Production paths never run on test fixtures |
| D14 | historical reads | The gateway serves hot reads of the ACTIVE run only; historical evidence is the viewer's job over the shared `trace_root`. No history endpoints on the gateway |
| D15 | run cancel | `POST /runs/{id}/cancel` (no body): cooperative-cancel every RUNNING task, wait for terminal settlement (settle timeout defaults to 150s — it must exceed the slowest task window with slack, pitfalls 18.4), then close the run as cancelled. The escape hatch for wedged runs (finish 409s on non-terminal tasks; a gateway restart kills the dispatcher, pitfalls 16.9) |
| D16 | run provenance | `StartRunRequest` carries optional `intent` / `metadata`, recorded verbatim on the registry entry (business provenance; the gateway never interprets them). Additive to D4 |
| D17 | streaming | `POST /governed/llm-chat-stream`: same body as llm-chat, SSE frames `{delta\|done\|error}`; the done frame carries `call_id` + `usage` (include_usage forced on). Serves token-level streaming for remote chat-model shells; 422 with guidance when the client has no `stream()` |
| D18 | OpenAI-compatible surface | `GET /v1/models` + `POST /v1/chat/completions`: a protocol envelope over the SAME governed call path as `/governed/llm-chat` (envelope swap only — body `model` → client registry key, `messages` verbatim, opaque kwargs transport, response re-wrapped; governance unchanged underneath). Attribution: `X-Governance-Run-Id` / `-Task-Id` / `-Purpose` headers, with the `@active` sentinel resolving PER REQUEST to the active user run (a credential-static header with runtime-dynamic resolution; degrades to ambient when no run is active); absent headers route to the ambient run (D2). `/v1/models` returns the strict OpenAI models-list shape (the n8n OpenAI credential test and model dropdown both read it). OpenAI error envelopes; an OpenAI SDK retry is a fresh governed call and may bill twice. Compatibility note (verified n8n 2.35.7): the built-in OpenAI Chat Model defaults to the Responses API — "Use Responses API" must be OFF for the /v1 chat-completions surface, and Workflow-Tool sub-workflows must be published. Purpose: OpenAI-compatible clients without a custom integration node — keeping consumer node packages free of `@langchain/core` |

## 2. Architecture

```
remote orchestrator (n8n canvas, scripts, CI, any HTTP client)
   │  HTTP only (bearer token)
   ▼
ordigovernance-gateway (the WRITE path)
   call plane   /governed/llm-chat, /governed/tool-call
   task plane   /runs, /runs/{id}/tasks, finish, vocabulary
   HITL plane   /runs/{id}/hitl/{pause,resume,retry,receipt}
   composites   /runs/{id}/composites[/{cid}]
   openai       /v1/models, /v1/chat/completions (D18: envelope over
                the governed call plane for OpenAI-compatible clients)
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
  a contextvar; a composite failure lands on its own status
  endpoint, never on the run.
- **OpenAI-compatible surface (D18)**: `/v1/*` is an envelope swap
  over the same governed call path — no second governance path, no
  framework knowledge. n8n's built-in OpenAI Chat Model consumes it
  directly (credential Base URL `http://<gateway>/v1`), which keeps
  consumer node packages free of `@langchain/core`.

## 3. Integration growth model

The main repository grows only with **generic capability**. An
integration with an orchestrator ecosystem lives in that ecosystem's
own repository and consumes the gateway over HTTP — the main
repository gains nothing unless the generic surface itself needs new
capability, which is a design decision (D18 was one; a prospective
D19 covers two-phase external tool execution).

| integration | where it lives | what the main repo gained |
|---|---|---|
| n8n | [n8n-nodes-ordigovernance](https://github.com/orditect/n8n-nodes-ordigovernance) (npm, MIT) | nothing — D18 was generic-surface work |
| langgraph / deepagents | their own bridge repositories (MIT) | nothing |
| future orchestrators | their ecosystems | nothing until a generic-surface need appears |

The corollary for positioning: the gateway is **not** "the n8n
bridge" — n8n is its first-class consumer. Wire identifiers stay
orchestrator-neutral (D8: `remote-chat` / `remote-call-`), and
ecosystem-specific knowledge (node UX, release flows, field lessons)
belongs to the consumer repositories, not here.