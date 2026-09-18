# ordigovernance-testing

Golden trace normalization (`normalize_bundle`, `diff_summary`), the
conformance kit, in-memory hot-path fixtures and deterministic mocks
(`ScriptedLLMClient`, mock tool handlers) for zero-infrastructure
verification.

Conformance profiles:

- `run_producer_profile` — one governed run's memo/archive/call-id
  evidence shapes;
- `run_engine_memo_profile` — a memo engine's behavioral contract
  (result/origin shape, origin vocabulary, no fabrication, producer
  discipline, seq totality). Engine tiers call this against their own
  `memo_layer_factory` to self-certify ordigovernance compatibility;
  reuse itself is engine semantics and is NOT required — fabrication
  is forbidden.