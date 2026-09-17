# Release smoke runbook (manual, ~15 min)

Run this on a real n8n + real gateway + real endpoint before every
npm publish / gateway release. CI covers contracts; this covers the
real runtime behavior mocks cannot see (instanceof probes, expression
evaluation, credential wiring, dispatcher liveness).

## Setup

1. `scripts/dev-gateway.sh` (terminal 1, keep running)
2. `scripts/dev-n8n.sh` (terminal 2, keep running)
3. Browser: hard-refresh http://localhost:5678

## Checks (import from workflows/, or reuse saved canvases)

| # | Canvas | Pass criterion |
|---|---|---|
| 1 | m3-hitl-approval.json | pause -> cancelled visible in hot record; curl resume -> awaitDecision returns `approved` with new execution_id; receipt + prevs verified |
| 2 | m4-narrative.json | all five tasks `succeeded`; writer output `input_pins` has both researcher ids; audit shows no `search-*` line for writer |
| 3 | m5-quality-gate.json | composite `status: succeeded`; `outcome.iterations` >= 1; audit shows per-iteration `write-*` / `review-*` generations |

## Audit spot-check (any run)

    RUN=$(curl -s "$GW/runs" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['run_id'])")
    cat data/gateway-runs/$RUN/trace/audit.ndjson | python3 -c "
    import sys, json
    for line in sys.stdin:
        d = json.loads(line)['data']
        print(d['event_type'], '|', d.get('event_id',''))"

Every llm_call must carry usage tokens; every generation must have
its memsave-...-90 archive row.

## If any check fails

Do not publish. File the failure as a new pitfall (numbered, with
the locking test named) before fixing.