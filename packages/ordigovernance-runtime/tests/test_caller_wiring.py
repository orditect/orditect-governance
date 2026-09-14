import asyncio

from ordigovernance.runtime.orchestration.caller_wiring import DependencyWiring


def run(coro):
    return asyncio.run(coro)


class FakeGovernor:
    def __init__(self, ready_after_polls: int = 2):
        self.registered = []
        self.notified = []
        self._ready_after = ready_after_polls
        self._polls = 0

    async def register_dependency(self, task_id, parents, *,
                                  primary_parent=None):
        self.registered.append((task_id, list(parents), primary_parent))

    async def notify_task_terminal(self, task_id, status):
        self.notified.append((task_id, status))

    async def get_ready_tasks(self):
        self._polls += 1
        return ["merge"] if self._polls > self._ready_after else []


class FakeInitIO:
    def __init__(self):
        self.initialized = []

    async def initialize_task(self, task_id, *, initial_status="pending"):
        self.initialized.append((task_id, initial_status))


def test_register_fan_in_with_initialize():
    gov, io = FakeGovernor(), FakeInitIO()
    wiring = DependencyWiring(gov, task_io=io)
    run(wiring.register_fan_in("merge", ["a", "b"], primary_parent="a",
                               initialize=True))
    assert io.initialized == [("merge", "pending")]
    assert gov.registered == [("merge", ["a", "b"], "a")]


def test_initialize_requires_task_io():
    wiring = DependencyWiring(FakeGovernor())
    try:
        run(wiring.register_fan_in("merge", ["a"], initialize=True))
    except RuntimeError as e:
        assert "requires task_io" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_notify_and_wait_ready():
    gov = FakeGovernor(ready_after_polls=2)
    wiring = DependencyWiring(gov)
    run(wiring.notify_terminal("a", "succeeded"))
    assert gov.notified == [("a", "succeeded")]
    ready = run(wiring.wait_ready("merge", timeout=5.0, poll_interval=0.01))
    assert ready == "merge"


def test_wait_ready_timeout():
    gov = FakeGovernor(ready_after_polls=10**9)
    wiring = DependencyWiring(gov)
    try:
        run(wiring.wait_ready("merge", timeout=0.05, poll_interval=0.01))
    except TimeoutError as e:
        assert "never became ready" in str(e)
    else:
        raise AssertionError("expected TimeoutError")