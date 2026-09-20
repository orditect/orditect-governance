## Migration from orditect-components

The open tier is **mechanism-direct by default**: without an injected
memo engine, every logical call really executes and is really charged
in every generation — repeated spend is explicit by construction, and
the quickstart makes it visible. Two consequences when migrating from
the legacy monorepo:

- A run that previously relied on memo reuse will now re-execute its
  world reads and LLM calls on every generation; size budgets
  accordingly (a run with a tight budget cap may now hit the cap).
- Routing policies (stub / sandbox / allow, llm freeze) are engine
  semantics: they activate only when an engine is injected via the
  assembly factories; without one, the passthrough resolver keeps
  every call site's declared behavior.

The conformance kit and the acceptance ground
(`examples/acceptance/`) run identically on both tiers; engine
semantics are verified by the engine tier's own suite.

# examples/gateway_n8n — reference registry + demo environment

Reference deployment for the n8n bridge: a registry the gateway loads
at boot (`registry.py`), a minimal cold-path viewer host over the
shared trace root (`viewer_app.py`), and a docker compose stack
(`compose/`) wiring redis + gateway + viewer + n8n together.

The narrative mirrors `examples/acceptance`: researcher world reads,
a writer composing drafts, an LLM reviewer whose parsed SCORE line
drives the quality gate, and a publisher pinning its producer
generation. The world is deterministic (mock search handler); every
model call is a real governed endpoint call.

## Layout

    examples/gateway_n8n/
      registry.py          build_registry(): tools / impls / composites
      viewer_app.py        read-only evidence host over the trace root
      compose/             docker compose demo stack + runbook env

## Prerequisites

- The `orditect` framework checkout as a SIBLING of this repository
  (the compose build context is the parent directory of both).
- An OpenAI-compatible endpoint: OPENAI_BASE_URL / OPENAI_API_KEY.

## Compose quickstart

    cd examples/gateway_n8n/compose
    cp .env.example .env        # fill OPENAI_BASE_URL / OPENAI_API_KEY
    docker compose up --build

Services:

| service | URL | role |
|---|---|---|
| gateway | http://localhost:8180 | WRITE path (execution front) |
| viewer  | http://localhost:8181 | READ path (evidence) |
| n8n     | http://localhost:5678 | orchestration canvas |
| redis   | localhost:6379        | hot path |

Smoke: `curl http://localhost:8180/healthz`

## Dev stack without docker

    scripts/dev-stack.sh    # gateway :8180 + viewer :8181 + n8n :5678,
                            # one terminal each, with a readiness probe

## Walkthrough (curl; the n8n nodes automate the same calls)

    TOKEN=<your GATEWAY_AUTH_TOKEN>
    H="Authorization: Bearer $TOKEN"
    G=http://localhost:8180

    # 1. start a run (single active run per gateway)
    curl -X POST $G/runs -H "$H" -H 'Content-Type: application/json' \
      -d '{"run_id": "demo-1"}'

    # 2. fan out researchers (scheduling is the orchestrator's job:
    #    the gateway writes upstream as EVIDENCE edges, never waits)
    for t in ev-battery ev-charging ev-supply; do
      curl -X POST $G/runs/demo-1/tasks -H "$H" \
        -H 'Content-Type: application/json' \
        -d "{\"task_id\": \"$t\", \"impl\": \"researcher\",
             \"params\": {\"topic\": \"$t\"}}"
    done

    # 3. poll until terminal, then compose downstream
    curl $G/runs/demo-1/tasks/ev-battery -H "$H"

    curl -X POST $G/runs/demo-1/tasks -H "$H" \
      -H 'Content-Type: application/json' \
      -d '{"task_id": "writer", "impl": "writer",
           "params": {"upstream": ["ev-battery","ev-charging","ev-supply"]},
           "upstream": ["ev-battery","ev-charging","ev-supply"]}'

    curl -X POST $G/runs/demo-1/tasks -H "$H" \
      -H 'Content-Type: application/json' \
      -d '{"task_id": "reviewer", "impl": "reviewer",
           "params": {"producer_id": "writer"}, "upstream": ["writer"]}'

    curl -X POST $G/runs/demo-1/tasks -H "$H" \
      -H 'Content-Type: application/json' \
      -d '{"task_id": "publisher", "impl": "publisher",
           "params": {"producer_id": "writer"}, "upstream": ["writer"]}'

    # 4. finish (409 while any task is non-terminal)
    curl -X POST $G/runs/demo-1/finish -H "$H"

    # 5. evidence (cold path; hot reads close at finish)
    open http://localhost:8181/
    curl "http://localhost:8181/api/runs/demo-1/validate?root_id=demo-1"

## Composite (quality gate as one call)

    curl -X POST $G/runs/demo-1/composites -H "$H" \
      -H 'Content-Type: application/json' \
      -d '{"name": "quality_gate_pair",
           "params": {"producer_params": {"upstream": ["ev-battery"]},
                      "upstream": ["ev-battery"], "threshold": 80}}'
    curl $G/runs/demo-1/composites/<composite_id> -H "$H"

## n8n hookup

The dedicated n8n nodes land with the n8n-nodes-ordigovernance
package (Phase 6). Until then the built-in HTTP Request node covers
every endpoint above: base URL `http://gateway:8180` inside the
compose network (`http://host.docker.internal:8180` for an external
n8n), header `Authorization: Bearer <token>`. Design-time vocabulary
for node dropdowns: `GET /runs/ambient/vocabulary`.

## Memory-mode alternative (no docker)

    pip install -e "./packages/ordigovernance-gateway[memory]" \
        --config-settings editable_mode=compat
    export GATEWAY_AUTH_TOKEN=... OPENAI_BASE_URL=... OPENAI_API_KEY=...
    export GATEWAY_REGISTRY_MODULE=examples.gateway_n8n.registry:build_registry
    export GATEWAY_MODEL_CLIENTS='{"research":{"model":"<model>","resource":"llm_research"},"writing":{"model":"<model>","resource":"llm_writing"},"publish":{"model":"<model>","resource":"llm_writing"}}'
    export GATEWAY_SEMAPHORES='{"task_execution":8,"llm_research":2,"llm_writing":1,"web_search":2,"memo_store":2}'
    uvicorn ordigovernance.gateway.app:build_app --factory --port 8180
    python -m examples.gateway_n8n.viewer_app   # second shell

## Boundary note

The gateway source tree never imports this package; the registry is
injected at deployment time through GATEWAY_REGISTRY_MODULE (the
module channel). Production deployments should ship their registry in
their own installed package and may additionally register entry
points under `ordigovernance.gateway.tools` / `.impls` /
`.composites`.
```

新增： `examples/gateway_n8n/compose/docker-compose.yml`
```yaml
# Gateway n8n demo stack: redis (hot path) + gateway (write path) +
# viewer (read path) + n8n (orchestration canvas).
#
# Build context discipline: the context is the PARENT directory of the
# two sibling checkouts (orditect/ and orditect-governance/), so the
# Dockerfile can install the framework from source. Run from this
# directory: docker compose up --build

x-demo-build: &demo-build
  build:
    context: ../../..
    dockerfile: orditect-governance/examples/gateway_n8n/compose/Dockerfile

services:
  redis:
    image: redis:7
    ports:
      - "6379:6379"

  gateway:
    <<: *demo-build
    environment:
      GATEWAY_AUTH_TOKEN: ${GATEWAY_AUTH_TOKEN:-demo-token}
      GATEWAY_REDIS_URL: redis://redis:6379/0
      GATEWAY_TRACE_ROOT: /data/runs
      GATEWAY_REGISTRY_MODULE: examples.gateway_n8n.registry:build_registry
      GATEWAY_SEMAPHORES: '{"task_execution": 8, "llm_research": 2, "llm_writing": 1, "web_search": 2, "memo_store": 2}'
      GATEWAY_MODEL_CLIENTS: '{"research": {"model": "${PROBE_MODEL:-gpt-4o-mini}", "resource": "llm_research"}, "writing": {"model": "${PROBE_MODEL:-gpt-4o-mini}", "resource": "llm_writing"}, "publish": {"model": "${PROBE_MODEL:-gpt-4o-mini}", "resource": "llm_writing"}}'
      OPENAI_BASE_URL: ${OPENAI_BASE_URL:?set OPENAI_BASE_URL in .env}
      OPENAI_API_KEY: ${OPENAI_API_KEY:?set OPENAI_API_KEY in .env}
    volumes:
      - trace-data:/data
    ports:
      - "8180:8180"
    command: >-
      uvicorn ordigovernance.gateway.app:build_app --factory
      --host 0.0.0.0 --port 8180
    depends_on:
      - redis

  viewer:
    <<: *demo-build
    environment:
      GATEWAY_TRACE_ROOT: /data/runs
      VIEWER_PORT: "8181"
    volumes:
      - trace-data:/data
    ports:
      - "8181:8181"
    command: python -m examples.gateway_n8n.viewer_app
    depends_on:
      - gateway

  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    environment:
      N8N_SECURE_COOKIE: "false"
      N8N_HOST: localhost
      N8N_PORT: "5678"
    ports:
      - "5678:5678"
    volumes:
      - n8n-data:/home/node/.n8n

volumes:
  trace-data:
  n8n-data:
```

新增： `examples/gateway_n8n/compose/Dockerfile`
```dockerfile
# Demo image: framework (orditect, sibling checkout) + governance
# stack + gateway. The build context is the PARENT directory of the
# two sibling checkouts, so both source trees are visible.
FROM python:3.12-slim

WORKDIR /app

COPY orditect/ /app/orditect/
COPY orditect-governance/ /app/orditect-governance/

RUN pip install --no-cache-dir \
    -e /app/orditect/packages/protocol \
    -e /app/orditect/packages/core \
    -e /app/orditect/packages/flow \
    -e /app/orditect/packages/stream \
    -e /app/orditect/packages/adapter-memory \
    -e /app/orditect/packages/adapter-local \
    -e /app/orditect/packages/adapter-ui \
    -e /app/orditect/packages/bridge-openai \
    -e /app/orditect-governance/packages/ordigovernance-api \
    -e /app/orditect-governance/packages/ordigovernance-runtime \
    -e /app/orditect-governance/packages/ordigovernance-testing \
    -e /app/orditect-governance/packages/ordigovernance-viewer \
    -e /app/orditect-governance/packages/ordigovernance-bridges-direct \
    -e "/app/orditect-governance/packages/ordigovernance-gateway[memory]"

# examples/ is not an installed distribution; the demo registry and
# the viewer host import it from the source tree.
ENV PYTHONPATH=/app/orditect-governance
```

新增： `examples/gateway_n8n/compose/.env.example`
```bash
# Copy to .env and fill in. Compose interpolates these into the stack.
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
PROBE_MODEL=gpt-4o-mini
GATEWAY_AUTH_TOKEN=change-me
```

新增： `examples/gateway_n8n/compose/Dockerfile.dockerignore`
```
**/.git
**/data
**/__pycache__
**/*.egg-info
**/.pytest_cache
**/.ruff_cache
**/node_modules
**/.env