"""Layered probe for real-endpoint bridge verification (.env).

Run this BEFORE wiring the langgraph/deepagents bridges to a real
endpoint. Each layer isolates one failure class so a breakage is
attributed to exactly one component instead of surfacing three frames
away inside a react loop (docs/pitfalls.md 6):

  layer 1  bare GovernedLLMClient: connectivity, response usage, and
           the single most important unknown -- whether the client
           forwards the endpoint-native `tools` parameter. If it does
           not, every real tool-calling loop fails SILENTLY (the model
           answers with plain text and nothing ever complains).
  layer 2  full langgraph react loop through GovernedAgent +
           PassthroughTracked atoms + build_react_agent: tool
           execution through the governed plane, naming-discipline
           call ids, origins, archive, and usage billing on the audit
           stream.
  layer 3  same contract over build_tracked_agent (deepagents).

Usage:
    pip install python-dotenv   # optional; plain env vars also work
    PROBE_LAYER=1 python scripts/probe_bridge_env.py
    PROBE_LAYER=all python scripts/probe_bridge_env.py

Required environment (.env or exported):
    OPENAI_BASE_URL   OpenAI-compatible endpoint
    OPENAI_API_KEY    API key
Optional:
    PROBE_MODEL       model name (default: gpt-4o-mini)
    PROBE_LAYER       1 | 2 | 3 | all (default: 1)
    PROBE_TIMEOUT     per-layer timeout in seconds (default: 180)

Exit code 0 when every executed layer passed (warnings allowed),
1 on any failure, 2 on configuration errors.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "probe-agent"

def _count_env_keys(env_path: Path) -> int:
    """Count definition lines in one .env file (diagnostics only)."""
    count = 0
    try:
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                count += 1
    except OSError:
        pass
    return count


def _load_env_file() -> tuple[str, int]:
    """Load the repo-root .env; python-dotenv when installed, else built-in.

    The probe must work with zero extra dependencies, so a missing
    python-dotenv package falls back to a minimal parser (KEY=value
    lines, optional `export` prefix, optional quotes, BOM/CRLF
    tolerant). Existing process environment always wins, matching
    python-dotenv's no-override default. Returns (source, keys) for
    the startup diagnostic line.
    """
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return f"no .env found at {env_path}", 0
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return f"{env_path} (python-dotenv)", _count_env_keys(env_path)
    except ImportError:
        pass
    loaded = 0
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return f"{env_path} (built-in parser)", loaded

_SEARCH_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}

_REACT_PROMPT = (
    "Use the search tool exactly once to look up 'EV battery news', "
    "then answer with one short sentence."
)


class _Probe:
    """Collects per-check verdicts; the exit code derives from them."""

    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def ok(self, msg: str) -> None:
        print(f"  PASS  {msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        print(f"  WARN  {msg}")

    def fail(self, msg: str) -> None:
        self.failures += 1
        print(f"  FAIL  {msg}")


def _read_audit(trace_dir: Path) -> list[dict]:
    """Tolerant audit read: flat or data-wrapped rows, partial tail skipped."""
    path = trace_dir / "audit.ndjson"
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(row.get("data", row))
    return rows


def _trace_dir(layer: str) -> Path:
    # Per-layer parent: build_run_context cleans trace_dir.parent, so
    # layers must never share a parent (docs/pitfalls.md 11).
    return ROOT / "data" / "probe" / f"layer-{layer}" / "trace"


async def _make_resources(layer: str):
    """Fresh in-memory hot path plus a run context for one layer."""
    from ordigovernance.runtime.lifecycle.run_context import (
        build_run_context,
    )
    from ordigovernance.testing.hot_path import build_memory_hot_path

    async def _unknown_task(task_id: str):
        raise KeyError(f"unknown task_id {task_id}")

    hot = build_memory_hot_path({
        "task_execution": 2,
        "llm": 1,
        "web_search": 1,
        "memo_store": 1,
    })
    resources = await build_run_context(
        hot,
        trace_dir=_trace_dir(layer),
        budget_scope=f"probe:{layer}",
        budget_max_units=100000,
        task_factory=_unknown_task,
    )
    return hot, resources


def _make_clients(base_url: str, api_key: str, model: str,
                  hot: dict, resources) -> dict:
    from ordigovernance.bridges.direct.llms import build_client_registry

    return build_client_registry(
        base_url,
        api_key=api_key,
        clients={"research": {"model": model, "resource": "llm"}},
        governor=hot["governor"],
        budget=resources.budget,
        store=resources.store,
    )


# ---- layer 1: bare client -----------------------------------------------------


async def layer1(probe: _Probe, base_url: str, api_key: str,
                 model: str) -> None:
    from ordigovernance.runtime.lifecycle.run_context import (
        teardown_run_context,
    )

    hot, resources = await _make_resources("1")
    try:
        llms = _make_clients(base_url, api_key, model, hot, resources)
        client = llms["research"]

        resp = await client.chat(
            messages=[{"role": "user", "content": "Reply with: pong"}],
            call_id="probe-l1-ping",
        )
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        if message.get("content"):
            probe.ok(f"basic chat answered ({message['content'][:40]!r})")
        else:
            probe.fail(f"basic chat returned no content: {resp!r}")
        if isinstance(resp.get("usage"), dict) \
                and resp["usage"].get("total_tokens"):
            probe.ok(f"usage present ({resp['usage']})")
        else:
            probe.warn(
                "response carries no usage: token-based billing will "
                "charge the cost_fn fallback (check the audit payload)"
            )

        resp = await client.chat(
            messages=[{"role": "user",
                       "content": "Call the web_search tool with "
                                  "query 'openai'. Do not answer in text."}],
            call_id="probe-l1-tools",
            tools=[_SEARCH_TOOL_SPEC],
        )
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        if message.get("tool_calls"):
            probe.ok("client forwards the tools parameter "
                     "(model emitted tool_calls)")
        else:
            probe.fail(
                "no tool_calls in the response: either GovernedLLMClient "
                "does not forward the tools parameter (fix the orditect "
                "bridge before continuing) or the endpoint/model does "
                "not support tool calling"
            )
    finally:
        await teardown_run_context(resources)
    print(f"  trace bundle: {_trace_dir('1')}")


# ---- layers 2/3: full react loops ---------------------------------------------


def _search_args_schema():
    from pydantic import BaseModel

    class SearchArgs(BaseModel):
        query: str

    return SearchArgs


async def _react_loop(probe: _Probe, layer: str, base_url: str,
                      api_key: str, model: str, *, deepagents: bool) -> None:
    from ordigovernance.api.naming import SEQ_AGENT_BASE
    from ordigovernance.runtime.agent.governed_agent import GovernedAgent
    from ordigovernance.runtime.atoms import (
        PassthroughTrackedLLM,
        PassthroughTrackedToolSet,
    )
    from ordigovernance.runtime.lifecycle.run_context import (
        teardown_run_context,
    )
    from ordigovernance.runtime.tools.governed_tools import GovernedToolSet
    from ordigovernance.testing import mock_tools

    hot, resources = await _make_resources(layer)
    handler_calls: list[str] = []

    async def web_search(query: str) -> dict:
        handler_calls.append(query)
        return {"query": query,
                "snippets": [f"probe snippet for {query!r}"]}

    try:
        llms = _make_clients(base_url, api_key, model, hot, resources)
        tools = GovernedToolSet(
            hot["governor"], resources.budget, resources.store,
            task_id=TASK_ID,
            memory_read_handler=mock_tools.memory_read,
            memory_write_handler=mock_tools.memory_write,
            memory_resource="memo_store",
        )
        tools.register("search", web_search, resource="web_search",
                       event_type="tool_call")

        tool_specs = {"search": {
            "description": "Search the web for current information.",
            "args_schema": _search_args_schema(),
        }}

        class _ProbeImpl:
            async def run(self, ctx) -> dict:
                if deepagents:
                    from ordigovernance.bridges.deepagents import (
                        build_tracked_agent,
                    )

                    agent = build_tracked_agent(
                        PassthroughTrackedLLM(ctx, "research"),
                        PassthroughTrackedToolSet(ctx),
                        tool_specs,
                        system_prompt="You are a careful researcher.",
                    )
                else:
                    from ordigovernance.bridges.langgraph import (
                        build_react_agent,
                    )

                    agent = build_react_agent(
                        PassthroughTrackedLLM(ctx, "research"),
                        PassthroughTrackedToolSet(ctx),
                        tool_specs,
                    )
                state = await agent.ainvoke(
                    {"messages": [("user", _REACT_PROMPT)]})
                final = state["messages"][-1].content
                result = {"final": final, "origins": dict(ctx.origins)}
                await ctx.archive(result, pins={})
                return result

        agent = GovernedAgent(
            impl=_ProbeImpl(), task_io=hot["storage"], tools=tools,
            llms=llms, archive_backend=tools, memo_scope="probe",
        )
        await hot["storage"].initialize_task(
            TASK_ID, initial_status="running")
        result = await agent.execute(TASK_ID)

        if handler_calls:
            probe.ok(f"tool executed through the governed plane "
                     f"({len(handler_calls)} call(s): {handler_calls})")
        else:
            probe.fail("the react loop never invoked the search tool")
        origins = result.get("origins") or {}
        if "search" in origins:
            probe.ok(f"origins recorded: {origins['search']}")
        else:
            probe.fail(f"no origin recorded for 'search': {origins}")
        if result.get("final"):
            probe.ok(f"final answer: {result['final'][:60]!r}")
        else:
            probe.warn("final answer is empty")

        eid = (await hot["storage"].get_task(TASK_ID))["execution_id"]
        audit = _read_audit(_trace_dir(layer))
        tool_ids = [e.get("event_id", "") for e in audit
                    if str(e.get("event_id", "")).startswith(
                        f"search-{TASK_ID}-{eid}-")]
        if tool_ids:
            probe.ok(f"governed tool call id lands in audit: {tool_ids[0]}")
        else:
            probe.fail(
                f"no audit event named search-{TASK_ID}-{eid}-*; "
                f"expected the agent band at seq {SEQ_AGENT_BASE + 1}")
        billed = [e for e in audit
                  if isinstance(e.get("payload"), dict)
                  and (e["payload"].get("usage") or {}).get("total_tokens")]
        if billed:
            probe.ok(f"usage billed on {len(billed)} audit event(s)")
        else:
            probe.warn("no audit event carries usage: verify the "
                       "endpoint returns usage and the client records it")
    finally:
        await teardown_run_context(resources)
    print(f"  trace bundle: {_trace_dir(layer)}")


async def layer2(probe: _Probe, base_url: str, api_key: str,
                 model: str) -> None:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        probe.warn("langgraph not installed; skipping layer 2")
        return
    await _react_loop(probe, "2", base_url, api_key, model,
                      deepagents=False)


async def layer3(probe: _Probe, base_url: str, api_key: str,
                 model: str) -> None:
    try:
        import deepagents  # noqa: F401
    except ImportError:
        probe.warn("deepagents not installed; skipping layer 3")
        return
    await _react_loop(probe, "3", base_url, api_key, model,
                      deepagents=True)


# ---- main ------------------------------------------------------------------------

_LAYERS = {"1": layer1, "2": layer2, "3": layer3}


def main() -> int:
    source, loaded = _load_env_file()
    print(f"env: {source} ({loaded} key(s))")

    # OPENAI_API_BASE is the widespread alias used by vLLM/LiteLLM
    # deployments; either name configures the probe.
    base_url = (os.environ.get("OPENAI_BASE_URL")
                or os.environ.get("OPENAI_API_BASE"))
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("PROBE_MODEL", "gpt-4o-mini")
    layer = os.environ.get("PROBE_LAYER", "1")
    timeout = float(os.environ.get("PROBE_TIMEOUT", "180"))

    missing = [name for name, value in (
        ("OPENAI_BASE_URL", base_url), ("OPENAI_API_KEY", api_key))
        if not value]
    if missing:
        print(f"FAIL  missing environment: {', '.join(missing)}")
        if loaded:
            print(f"      .env was read ({loaded} key(s)) but does not "
                  f"define them; check the key names")
        else:
            print(f"      expected a .env at {ROOT / '.env'} with:\n"
                  f"        OPENAI_BASE_URL=https://.../v1\n"
                  f"        OPENAI_API_KEY=sk-...")
        return 2

    probe = _Probe()
    selected = tuple(_LAYERS) if layer == "all" else (layer,)
    for name in selected:
        fn = _LAYERS.get(name)
        if fn is None:
            print(f"unknown PROBE_LAYER {name!r}; expected 1|2|3|all")
            return 2
        print(f"=== layer {name} "
              f"({fn.__doc__.strip() if fn.__doc__ else name}) ===")
        try:
            asyncio.run(asyncio.wait_for(
                fn(probe, base_url, api_key, model), timeout=timeout))
        except Exception as e:
            probe.fail(f"layer {name} raised {type(e).__name__}: {e}")

    print()
    if probe.failures:
        print(f"FAIL  {probe.failures} check(s) failed, "
              f"{probe.warnings} warning(s)")
        return 1
    print(f"PASS  all checks passed ({probe.warnings} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())