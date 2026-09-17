# n8n-nodes-ordigovernance

n8n community nodes bridging to the **ordigovernance gateway** — the
HTTP execution front of the [orditect-governance](https://github.com/orditect/orditect-governance)
stack. Every model call, tool call and task action issued from n8n
lands as an audited, budgeted, idempotent governed call on the
gateway's hot path: semaphores, budgets, call_id idempotency, audit
events, execution generations and lineage pins, all inspectable in
the cold-path viewer.

Governance disclosure: this package is a pure HTTP client plus
format-translation shells. It never imports any governance or engine
package (those live in the orditect-governance repository under
SUL-1.0); n8n talks to the gateway over HTTP only. This package is
MIT-licensed.

## Install (custom nodes)

    # inside your n8n custom extensions directory (~/.n8n/custom)
    npm install n8n-nodes-ordigovernance
    # or, from a checkout:
    npm install /path/to/n8n-nodes-ordigovernance
    npm run build   # when installing from source

Restart n8n. The nodes appear under the "Ordigovernance" names.

**Peer dependency discipline**: `@langchain/core` is a peerDependency
(`>=1.0.0 <2.0.0`). n8n loads its own built-in copy; this package must
resolve to THE SAME instance, or `instanceof BaseChatModel` silently
fails and the AI Agent node rejects the model. Verify with:

    npm ls @langchain/core        # one single resolved copy
    # against n8n's own:
    npm ls -g @langchain/core     # or check inside your n8n install

Pin your install to the version your n8n ships when they disagree.

## Nodes

| node | kind | status |
|---|---|---|
| Ordigovernance Chat Model | supply-data (AI model) | M1 ✅ |
| Ordigovernance Run / Tool / Task / Approval | action | M2/M3/M4 ✅ canvas-verified |
| Ordigovernance Composite | action | M5 ✅ quality-gate converged [42, 92] |

### Ordigovernance Chat Model

A `BaseChatModel` whose every invocation is one governed call through
`POST /governed/llm-chat`:

- `Client` — a registered client name in the gateway registry
  (unknown names fail with a 422 listing the valid names);
- `Purpose` — the call_id purpose segment (naming discipline);
- options `Run ID` / `Task ID` — route into an active user run and
  attribute calls to an existing task hot record; empty means the
  gateway's ambient run with an ephemeral identity.

Bound tools, tool_choice, stop words and any extra bind kwargs are
forwarded to the gateway verbatim on every call; the unbound model
never carries tool specs. Assistant tool_calls survive the
round-trip in whichever shape the middleware left them
(`additional_kwargs.tool_calls` fallback included).

## M1 spike runbook

1. Start the gateway (memory mode, no redis):

   ```bash
   export GATEWAY_AUTH_TOKEN=dev-token
   export OPENAI_BASE_URL=... OPENAI_API_KEY=...
   export GATEWAY_MODEL_CLIENTS='{"research":{"model":"<model>","resource":"llm_research"}}'
   export GATEWAY_SEMAPHORES='{"task_execution":8,"llm_research":2,"web_search":2,"memo_store":2}'
   uvicorn ordigovernance.gateway.app:build_app --factory --port 8180
   ```

2. Start n8n with this package installed; create credentials
   (Ordigovernance Gateway API: base URL `http://localhost:8180`,
   token `dev-token`; the credential test hits `/healthz`).
3. Add an **AI Agent** node, attach **Ordigovernance Chat Model** as
   its model, client `research`, and run one conversation.

Acceptance (all must hold):

- `TrackedChatModel instanceof` n8n's built-in `BaseChatModel` is true
  (a peer-version mismatch surfaces HERE first);
- the AI Agent accepts the model node and completes a conversation;
- the gateway's ambient trace (`data/gateway-runs/ambient/trace/audit.ndjson`)
  shows an `llm_call` row whose `event_id` follows the naming
  discipline (`n8n-chat-...`) and carries token `usage`;
- a wrong token or wrong base URL fails with a readable error in the
  n8n UI, not a bare stack trace.

If any criterion fails, stop and report before Phase 6 work starts
(fallback: sidecar transport with plain HTTP nodes).

## Development

    npm install
    npm run build      # tsc + icons
    npm test           # shell contract tests (mocked gateway)
    npm run dev        # tsc --watch

Node note: the `n8n-workflow` dev dependency (types only) transitively
pulls `isolated-vm`, a native module requiring Node >= 22 with a
node-gyp postinstall. On Node 20, install with
`npm install --ignore-scripts` -- builds and tests never touch it;
prefer Node 22 when you also run n8n itself from this machine.

# n8n-nodes-ordigovernance

n8n community nodes for the [Ordigovernance](https://github.com/orditect/orditect-governance) governance gateway: every LLM chat, tool invocation, and task execution flows through the gateway's governance plane, producing auditable traces (event log, token usage, cost units, evidence pointers) for each run.

## Nodes

| Node | Kind | Purpose |
|---|---|---|
| Ordigovernance Chat Model | `ai_languageModel` sub-node | Drop-in chat model for the AI Agent (Tools Agent); each completion is a governed `/governed/llm-chat` call |
| Ordigovernance Run | action | Start / finish a governed run (`POST /runs`, `POST /runs/{id}/finish`) |
| Ordigovernance Tool | action | Direct governed tool call (`POST /governed/tool-call`) |
| Ordigovernance Task | action | Submit a task to a run and poll to terminal state (`POST /runs/{id}/tasks`, `GET /runs/{id}/tasks/{task_id}`) |
| Ordigovernance Approval | action | HITL operations: pause / resume / retry tasks in a run (dual-receipt), await a human decision via the evidence chain |
| Ordigovernance Composite | action | Start a drive-level composite (e.g. `quality_gate_pair`) and poll its outcome (`POST /runs/{id}/composites`, `GET /runs/{id}/composites/{cid}`) |

## Credentials

One credential type: **Ordigovernance Gateway API** (`ordigovernanceApi`)

- `Base URL`: gateway root, e.g. `http://localhost:8180`
- `Token`: bearer token matching the gateway's `GATEWAY_AUTH_TOKEN`

## Quick start (dev loop)

```bash
# 1. Build and install into n8n's custom extensions dir
cd n8n-nodes-ordigovernance
npm install && npm run build
rm -rf ~/.n8n/custom/node_modules/n8n-nodes-ordigovernance
npm install --prefix ~/.n8n/custom .
n8n start

# 2. Start the gateway with the demo registry (repo root)
scripts/dev-gateway.sh   # exports GATEWAY_REGISTRY_MODULE + PYTHONPATH, serves :8180
```

The demo registry provides tool `search` and impls `researcher`, `writer`,
`reviewer`, `publisher`, plus composite `quality_gate_pair`
(`examples/gateway_n8n/registry.py`).

## Gateway contract quick reference

These are the wire contracts the nodes were built and verified against
(M2, gateway v0.1). If your gateway version differs, adjust the path
constants at the top of each node file.

| Operation | Method & path | Body (required) |
|---|---|---|
| Start run | `POST /runs` | `{client, purpose, metadata}` |
| Finish run | `POST /runs/{id}/finish` | `{status, summary?}` |
| Tool call | `POST /governed/tool-call` | `{client, purpose, tool, inputs, run_id?, task_id?}` |
| Submit task | `POST /runs/{id}/tasks` | `{task_id, impl, params, upstream?, parent_task_id?, tools?}` |
| Poll task | `GET /runs/{run_id}/tasks/{task_id}` | — |
| HITL pause / resume / retry | `POST /runs/{id}/hitl/{action}` | pause/retry: `{task_id}`; resume: `{root_id}` |
| HITL receipt | `GET /runs/{id}/hitl/receipt/{action_id}` | — (404 = pending) |
| Start composite | `POST /runs/{id}/composites` | `{name, params?}` |
| Poll composite | `GET /runs/{id}/composites/{cid}` | — |
| LLM chat | `POST /governed/llm-chat` | `{client, purpose, messages, kwargs, run_id?, task_id?}` |

-Terminal approval statuses: `approved`, `rejected`, `expired`.
+Await-decision outcomes: `approved` (a new generation appeared on the task), `rejected` (the run finished without one).

## Troubleshooting: the M2 field guide

Every pitfall below was hit and fixed during the M2 end-to-end bring-up.
They are ordered by likelihood of recurrence.

### 1. `409 a run is already in progress: '<run-id>'`

The gateway allows **exactly one active run**. Any workflow that starts a
run but fails midway (red node) leaves the run open, and every subsequent
`POST /runs` is rejected.

Recovery (either):
- Enable **Finish Existing on Conflict** on the Run Start node
  (auto-cancels the stale run and retries once). Dev convenience; keep it
  off in production so conflicts stay loud.
- Cancel manually:
  ```bash
  curl -X POST "$GW/runs/<stale-run-id>/finish" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"status":"cancelled","summary":"stale cleanup"}'
  ```

Note: finishing a run that is no longer active returns `404 ... historical
evidence reads belong to the viewer cold path (D14)` — that is benign; the
run is already gone (e.g. after a gateway restart, since the active-run
state is in-memory).

### 2. `422 unknown tool/impl '...'; registered: []`

The running gateway process has **no registry loaded**. The registry is
deployment-injected; nothing is registered by default.

Fix — always start the gateway via `scripts/dev-gateway.sh`, which sets:

```bash
export GATEWAY_REGISTRY_MODULE="examples.gateway_n8n.registry:build_registry"
export PYTHONPATH="<repo-root>:$PYTHONPATH"   # so `examples` imports
```

Diagnosis recipe:

```bash
# Which process owns the port?
ss -tlnp | grep 8180
# Was it started with the registry env var?
cat /proc/<pid>/environ | tr '\0' '\n' | grep GATEWAY
# Does the registry import work at all?
python -c "from examples.gateway_n8n.registry import build_registry; \
  r=build_registry(); print(list(r.tools), list(r.impls))"
```

Classic trap: killing and restarting uvicorn in a shell **without** the
exports silently boots an empty-registry gateway. The n8n error only shows
up one node later.

### 3. `500 TypeError: web_search() missing 1 required positional argument: 'query'`

The tool-call body must carry arguments under **`inputs`** (gateway expands
them as handler kwargs: `tool_set.call(..., params=inputs, **inputs)`).
Sending `arguments` instead yields this exact 500. The node uses `inputs`;
if you hand-roll requests, match the schema.

### 4. Task submission `422 ... body.task_id / body.impl Field required`

Task submission requires a **client-generated `task_id`** and the registry
**`impl`** name — there is no `name` field. In the node, set Task ID to an
expression like `demo-task-{{ $now.toMillis() }}` (unique per execution).

### 5. Task polling `404 Not Found` on `GET /tasks/{id}`

The poll endpoint is **run-scoped**: `GET /runs/{run_id}/tasks/{task_id}`.
There is no global `/tasks/{id}` route.

### 6. `404 unknown run '=run-...'` — stray `=` in the Run ID field

In n8n, an expression field shows its value as `={{ ... }}`. If you typed
a leading `=` yourself while the field was already in expression mode, the
`=` becomes part of the literal value. Re-add the expression via
**Add Expression** and enter only the inner part:

```
$('Run Start').item.json.run_id ?? $('Run Start').item.json.id
```

### 7. Don't execute middle nodes standalone

Run-dependent nodes (Tool / Task / Finish) read `run_id` from the Run
Start node's output. Executing them standalone reuses the **previous**
execution's data — i.e. a finished run id — and fails. Always run the full
chain from the trigger. Related: expressions referencing a node execute
against that node's *latest* output, so after a successful run the values
are stale until the next start.

### 8. Tools Agent rejects the chat model: "requires Chat Model which supports Tools calling"

n8n's agent probes `typeof model.bindTools === 'function'` before accepting
a sub-node model. `TrackedChatModel` implements `bindTools` (via
`withConfig`) and forwards tool specs to the gateway on every call — if you
subclass or rewire it, keep that method intact.

### 9. `Response` objects are one-shot in tests

When mocking `fetch` for polling loops, use `mockImplementation(() =>
Promise.resolve(new Response(...)))` — a shared `mockResolvedValue(response)`
instance fails on the second poll with `Body has already been read`.

### 10. Reading the audit trail

Traces are **per-run**, not a single ambient file:

```
data/gateway-runs/<run_id>/trace/audit.ndjson
```

Each line is a WAL envelope; the payload lives under `data`:

```bash
cat data/gateway-runs/<run_id>/trace/audit.ndjson | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)['data']
    print(d['event_type'], '|', d['task_id'], '|',
          (d['payload'].get('usage') or {}).get('total_tokens', ''))"
```

Expect `tool_call` / `llm_call` / `memory_call` events with `cost_units`
and content-addressed evidence `pointer`s.

### 11. n8n port confusion

n8n itself serves `:5678`; the gateway in the dev setup serves `:8180`.
`Cannot POST /...` HTML error pages mean you curled n8n, not the gateway.


### 12. `%20%20...` in request URLs / `404 run ' run-...' is not active`

Whitespace around an n8n expression is LITERAL text: everything outside
`{{ }}` is sent verbatim, so an indented expression turns the run id
into `   run-...`, the gateway never matches it to an active run, and
you get the D14 404 one node later. The nodes trim ids defensively;
when hand-editing expressions, keep the field free of surrounding
whitespace (or drag the field in from the INPUT panel, which generates
a clean expression).

### 13. Impl receives default params; your Input never arrives

The submit body's payload field is `params`, not `input`. The gateway
schema ignores unknown fields (pydantic default), so a wrong field
name fails SILENTLY: the impl runs with its factory defaults (e.g.
topic="general") and nothing errors. Lock: the node sends `params`;
when hand-rolling requests, match the openapi schema. Node-side
task ids must also be unique per execution (`{{ $execution.id }}`
suffix) — the gateway deduplicates task ids across the shared hot
path, so a fixed id collides with previous runs (409).

### 14. `accepted: true` does not mean executed (the dead-queue trap)

HITL actions (pause / resume / retry) return `{"action_id": ..., "accepted": true}`
when the action enters the run's dispatcher queue — acceptance says nothing
about execution. If the run is already dead (gateway restarted, run finished,
session evaporated), the action lands in a dead queue: it is accepted, the
receipt stays pending forever, and nothing ever reruns. The receipt endpoint
guards this case (`404 ... HITL is valid only while the run lives`), the
action endpoint does not.

Rule: after any HITL action, verify with the execution receipt (poll until
non-404) AND the task hot record (`previous_execution_ids` grew). Both nodes
do this for you when "Wait for Execution Receipt" is on; when hand-rolling
requests, never treat `accepted` as evidence. Also: never restart the gateway
between starting a run and issuing its HITL actions.

### 15. Task IDs must be unique per execution

The gateway deduplicates task ids on the shared hot path: submitting a fixed
id (e.g. `researcher-m3`) in a NEW run collides with the record a PREVIOUS
run left behind — `409 ... is already submitted ... a rerun goes through
HITL retry` — even though the ids look run-scoped. Suffix every Task ID with
the n8n execution id:

    researcher-m3-{{ $execution.id }}

and have downstream nodes reference the upstream OUTPUT instead of repeating
the literal:

    {{ $('Submit Task').item.json.task_id }}

(Design-time, this expression renders red because the upstream node has no
output yet; it resolves at execution time. Drag the field in from the INPUT
panel instead of typing it.)

## Development

```bash
npm run build     # tsc + copy icons/json to dist/
npm test          # vitest: chat-model + node contract suites
```

Test suite covers: governed-call payloads, message conversion
(tool calls, ToolMessage pairing), credential type wiring, terminal-state
polling, 409 auto-recovery, and readable error surfacing (`[status]
body-snippet` convention — never raw stack traces to the UI).