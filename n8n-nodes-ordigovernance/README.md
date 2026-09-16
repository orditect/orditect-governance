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
(`>=0.3.0 <0.4.0`). n8n loads its own built-in copy; this package must
resolve to THE SAME instance, or `instanceof BaseChatModel` silently
fails and the AI Agent node rejects the model. Verify with:

    npm ls @langchain/core        # one single resolved copy
    # against n8n's own:
    npm ls -g @langchain/core     # or check inside your n8n install

Pin your install to the version your n8n ships when they disagree.

## Nodes

| node | kind | status |
|---|---|---|
| Ordigovernance Chat Model | supply-data (AI model) | M1 |
| Ordigovernance Run / Tool / Task / Approval / Composite | action | Phase 6/7 |

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