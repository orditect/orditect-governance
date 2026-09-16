# examples/acceptance — the open-tier acceptance ground

One full governed workflow on the mechanism-direct runtime:

    root -> fan-out (3 researchers)
         -> quality gate (writer/review, scripted 62 -> 88)
         -> HITL pause/resume surface (cooperative delay windows)
         -> one explicit reopen (researcher-1's second generation)
         -> publish (archive + pins)

Everything is real except the world and the model: mock tool handlers
plus a governed wrapper over ScriptedLLMClient. Semaphores, budget,
audit, generations, archive and pins all land in a trace bundle. No
redis, no LLM endpoint — the whole ground runs on the orditect
memory adapter.

    pip install ordigovernance-runtime ordigovernance-testing
    pip install orditect-adapter-memory orditect-core   # from the orditect checkout
    python -m examples.acceptance.app
    python -m examples.acceptance.selfcheck

## Real-endpoint run

Same narrative over a live model instead of the scripted client —
the functional verification ground for the governance stack under
real model behavior:

    cp ../../.env.example ../../.env   # OPENAI_BASE_URL / OPENAI_API_KEY
    python -m examples.acceptance.real_app
    python -m examples.acceptance.real_app --model my-model --threshold 85

What stays deterministic by design: the world (mock tool handlers —
swap TOOL_SPECS handlers to go fully live) and the HITL beats
(timing-injected cancels). What becomes real: every model call is a
real endpoint call billed by real tokens, and the review score is
parsed from the real review text — the quality gate converges when
the model says so, so the iteration count is evidence, not script.
A review without a parseable SCORE line fails loudly rather than
fabricating convergence.

Expect on the audit stream: every llm_call carrying real usage, the
HITL pause beat settling researcher-2 cancelled then resumed on a
second generation, researcher-1's explicit reopen charging its world
read twice, and the memsave/memload archive band closing the chain.

What the open tier's behavior looks like here: every logical call
really executes in every generation (the reopened researcher charges
its world read twice in the audit stream). The engine tier's memo
reuse and replay policy routing plug in behind the same assembly
surface and are verified by the engine tier's own suite.

The richer narrative demo (memo reuse beats, local replay, nested
intervals) lives in the engine tier's reference application.


    python -m examples.acceptance.real_app --model qwen-plus --threshold 85 --max-iterations 3

Defaults: model from PROBE_MODEL in .env (fallback gpt-4o-mini),
threshold 80, budget 100000 tokens. The review score is parsed from
the model's final line (SCORE: <0-100>); a missing or malformed
score fails loudly rather than fabricating convergence.

### Real Redis hot path (Batch 1)

Same narrative over the production storage/governor/quota instead of
the in-memory fakes (docs/pitfalls.md 1: the fakes only approximate
the Lua state machine, lease tokens and quota enforcement):

    docker run -d -p 6379:6379 redis:7
    python -m examples.acceptance.real_app --redis redis://localhost:6379/0

Watch the run log for the per-node hot-record lines: every reopened
node (researcher-1, researcher-2, writer/review iterations) must carry
its full previous_execution_ids chain back from the REAL Redis
storage, and the budget is clean by construction via a per-run scope.

## Pitfalls

See [docs/pitfalls.md](docs/pitfalls.md) before extending the
runtime, testing fixtures, or examples — every entry cost a real
debugging round.