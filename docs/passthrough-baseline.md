# Passthrough baseline: the open tier's default behavior

The open runtime ships mechanism-direct defaults. This document is
the behavioral snapshot engine tiers are compared against (the closed
tier's parity tests assert "identical except where the engine
changes it") — and the reference for anyone reading evidence produced
without an engine.

## What "passthrough" means, per surface

### memoize (no memo layer injected)

- Every call executes for real: `execute()` is invoked, the result is
  returned, origin is `"executed"`.
- `origins[purpose]` accumulates one `"executed"` entry per call.
- No memget/memput traffic exists: there is no cache to consult.
- Cross-generation behavior: identical inputs across generations
  re-execute and re-charge — repeated spend is explicit by
  construction (the quickstart's second-generation beat exists to
  make this visible).

### Policy resolution (no resolver injected)

- `PassthroughResolver` is in force: `active` is False, `table` is
  empty, `resolve()` returns the call site's declared reuse.
- Producer and internal call classes still refuse every override
  (resolved to "never").
- The stub mode is unreachable: external-tagged calls fire normally.

### Tracked atoms (passthrough)

- `PassthroughTrackedLLM.complete` / `.stream`: one governed call per
  invocation, seq from the agent band (1001+), call id
  `agent-llm-{task}-{eid}-{seq}`. Streams are never memoized.
- `PassthroughTrackedToolSet.call`: one governed call per invocation,
  call id `{tool}-{task}-{eid}-{seq}`, origins record `"executed"`.
- `reuse` is mechanism vocabulary here: "always"/"never" both
  execute (there is no cache to route).

### Replay driver (no drift engine injected)

- Replay mechanics run fully: local ids, submit/reopen sequences,
  baselines, evidence assembly.
- Reports carry `drift=None` — honest absence, never a fabricated
  verdict.

### Viewer reconciliation (no reconcile_fn injected)

- `/validate` returns run_rules findings only; zero DR-PIN-* rows.

## What engines change (and must never change)

Engines change routing and reuse SEMANTICS: memo hits, reused
origins, policy routing, drift verdicts. Engines never change
evidence production: archives, audit rows, generations, call ids and
origins land identically with or without an engine.