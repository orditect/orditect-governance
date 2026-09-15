# ordigovernance-bridges-direct

Direct bridge: governed runs without an orchestration framework.

Unlike the langgraph / deepagents bridges (thin format-translation
shells published from their own repositories under MIT), the direct
bridge is **assembly sugar for the runtime**, not an ecosystem adapter:
it wires the hot path (redis storage / governor / quota), the tool
registry and the run context for applications that orchestrate nodes
themselves. It ships from this repository because it is part of the
runtime's assembly surface, and it carries the repository's engine
license.

## Pieces

- `build_hot_path(redis_url, semaphores={...})` — storage, governor,
  quota and the semaphore registry over redis;
- `build_tool_set(governor, budget, store, task_id=..., tools={...})`
  — register business handlers as governed tools;
- `build_client_registry(base_url, clients={...}, ...)` — named
  governed LLM clients (names are business-chosen);
- `run(hot, trace_dir=..., root_id=..., drive=...)` — assemble the
  run context, delegate orchestration to `drive(resources)`, tear down.

Business facts (handlers, prompts, task builders, limits, endpoints,
drive order) always arrive as parameters; the bridge carries no
workflow vocabulary.