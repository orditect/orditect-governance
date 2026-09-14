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

What the open tier's behavior looks like here: every logical call
really executes in every generation (the reopened researcher charges
its world read twice in the audit stream). The engine tier's memo
reuse and replay policy routing plug in behind the same assembly
surface and are verified by the engine tier's own suite.

The richer narrative demo (memo reuse beats, local replay, nested
intervals) lives in the engine tier's reference application.


## Pitfalls

See [docs/pitfalls.md](docs/pitfalls.md) before extending the
runtime, testing fixtures, or examples — every entry cost a real
debugging round.