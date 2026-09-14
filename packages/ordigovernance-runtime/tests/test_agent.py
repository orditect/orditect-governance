"""GovernedAgent assembly (open-tier behavior).

Locks the assembly contracts (governed call ids, archive landing,
client registry, governor precedence) plus the open-tier memoize
behavior: with no memo layer injected, every generation really
executes and origins record "executed" — the repeated spend the
engine tier's reuse eliminates.
"""

import asyncio

from ordigovernance.runtime.agent.context import AgentContext
from ordigovernance.runtime.agent.governed_agent import GovernedAgent
from ordigovernance.api.task import GenerationMeta


def run(coro):
    return asyncio.run(coro)


class FakeTaskIO:
    def __init__(self, record):
        self._record = record

    async def get_task(self, task_id):
        return self._record

    async def update_task(self, task_id, patch):
        self._record.update(patch)


class FakeLLM:
    def __init__(self, name):
        self.name = name
        self.calls = []

    async def chat(self, messages, *, call_id, **kwargs):
        self.calls.append(call_id)
        return {"choices": [{"message": {"content": f"from-{self.name}"}}]}


class FakeTools:
    def __init__(self):
        self.store = {}
        self.calls = []

    async def call(self, name, *args, call_id, params=None, **kwargs):
        self.calls.append((name, call_id))
        return {"tool": name, "args": list(args)}

    async def memory_read(self, key, *, call_id, payload_fn=None):
        self.calls.append(("memory_read", call_id))
        return {"key": key, "value": self.store.get(key)}

    async def memory_write(self, key, value, *, call_id, payload_fn=None):
        self.calls.append(("memory_write", call_id))
        self.store[key] = value
        return {"key": key, "stored": True}


META = GenerationMeta(task_id="agent-a", eid="exec-gen1",
                      previous_eids=(), previous_status=None)


class EchoImpl:
    """Business impl: one memoized tool call + one llm call."""

    async def run(self, ctx: AgentContext) -> dict:
        vector, _ = await ctx.memoize(
            "vector", 1, {"q": "x"},
            lambda: ctx.tool_call("vector_db", "x", purpose="vector",
                                  seq=1, params={"q": "x"}),
        )
        resp = await ctx.llm_call("main", "analyze", 4,
                                  messages=[{"role": "user", "content": "go"}])
        result = {"vector": vector,
                  "analysis": resp["choices"][0]["message"]["content"]}
        await ctx.archive(result, pins={})
        return result


def make_agent(eid="exec-gen1", previous_status=None):
    tools = FakeTools()
    llm = FakeLLM("main")
    io = FakeTaskIO({"execution_id": eid, "previous_execution_ids": [],
                     "previous_status": previous_status})
    agent = GovernedAgent(
        impl=EchoImpl(), task_io=io, tools=tools, llms={"main": llm},
        archive_backend=tools, memo_scope="s",
    )
    return agent, tools, llm


def test_agent_executes_with_governed_call_ids():
    agent, tools, llm = make_agent()
    result = run(agent.execute("agent-a"))
    assert result["analysis"] == "from-main"
    assert result["vector"] == {"tool": "vector_db", "args": ["x"]}
    assert ("vector_db", "vector-agent-a-exec-gen1-1") in tools.calls
    assert llm.calls == ["analyze-agent-a-exec-gen1-4"]
    # archive write landed through the backend
    assert any(k.startswith("gen-result/agent-a/exec-gen1")
               for k in tools.store)


def test_open_tier_reexecutes_across_generations():
    """No memo layer: generation 2 re-executes the same logical call
    under its own eid, and origins record executed in both. This is
    the open-tier behavior the engine tier's reuse replaces."""
    agent1, tools, _ = make_agent(eid="exec-gen1")
    run(agent1.execute("agent-a"))
    agent2 = GovernedAgent(
        impl=EchoImpl(),
        task_io=FakeTaskIO({"execution_id": "exec-gen2",
                            "previous_execution_ids": ["exec-gen1"],
                            "previous_status": "succeeded"}),
        tools=tools, llms={"main": FakeLLM("main")},
        archive_backend=tools, memo_scope="s",
    )
    result2 = run(agent2.execute("agent-a"))
    tool_calls = [c for c in tools.calls if c[0] == "vector_db"]
    # Both generations really executed (cost-explicit by design).
    assert tool_calls == [("vector_db", "vector-agent-a-exec-gen1-1"),
                          ("vector_db", "vector-agent-a-exec-gen2-1")]
    assert result2["vector"] == {"tool": "vector_db", "args": ["x"]}
    # No memget traffic happened: there is no memo layer to consult.
    assert not any(cid.startswith("memget-") for _, cid in tools.calls)


def test_unknown_llm_client_raises_clearly():
    agent, _, _ = make_agent()

    class BadImpl:
        async def run(self, ctx):
            return await ctx.llm_call("nope", "x", 1, messages=[])

    agent._impl = BadImpl()
    try:
        run(agent.execute("agent-a"))
    except KeyError as e:
        assert "unknown llm client" in str(e)
    else:
        raise AssertionError("expected KeyError")


def test_executor_governor_takes_precedence():
    agent, _, _ = make_agent()
    sentinel = object()
    run(agent.execute("agent-a", governor=sentinel))
    assert agent.active_governor is sentinel
    # executor-free fallback: fresh agent without kwargs injection
    agent2, _, _ = make_agent()
    assert agent2.active_governor is agent2.governor