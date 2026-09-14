import asyncio

import pytest

from ordigovernance.runtime.stream.cancel_adapter import AsyncCancelToken
from ordigovernance.runtime.stream.pricing_fallback import zero_cost_on_none
from ordigovernance.runtime.stream.source_adapter import GovernedSource
from ordigovernance.runtime.stream.stage_aware_runner import run_stage_pipeline


def run(coro):
    return asyncio.run(coro)


class SyncToken:
    def __init__(self, value):
        self._value = value

    def is_cancelled(self):
        return self._value


class AsyncToken:
    def __init__(self, value):
        self._value = value

    async def is_cancelled(self):
        return self._value


def test_async_cancel_token_sync_and_async():
    assert run(AsyncCancelToken(SyncToken(True)).is_cancelled()) is True
    assert run(AsyncCancelToken(AsyncToken(False)).is_cancelled()) is False
    assert run(AsyncCancelToken(object()).is_cancelled()) is False


class FakeLLM:
    def __init__(self):
        self.kwargs = None

    async def stream(self, *, messages, call_id, cancel_token,
                     include_usage):
        self.kwargs = {"messages": messages, "call_id": call_id,
                       "cancel_token": cancel_token,
                       "include_usage": include_usage}
        for chunk in ["a", "b"]:
            yield chunk


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload


def test_governed_source_forwards_payload_and_wraps_token():
    llm = FakeLLM()
    source = GovernedSource(llm)

    async def collect():
        return [c async for c in source.stream(
            FakeRequest({"messages": [{"role": "user", "content": "x"}],
                         "call_id": "cid-1"}),
            cancel_token=SyncToken(False),
        )]

    chunks = run(collect())
    assert chunks == ["a", "b"]
    assert llm.kwargs["call_id"] == "cid-1"
    assert llm.kwargs["include_usage"] is False
    assert isinstance(llm.kwargs["cancel_token"], AsyncCancelToken)


class Envelope:
    def __init__(self, data):
        self.data = data


class Ev:
    def __init__(self, value):
        self.value = value


async def fake_events():
    yield Envelope({"stages": ["thinking", "report"]}), Ev("stream.start")
    yield Envelope({"kind": "content", "text": "why "}), Ev("stream.delta")
    yield Envelope({"name": "thinking"}), Ev("stage.end")
    yield Envelope({"kind": "content", "text": "final "}), Ev("stream.delta")
    yield Envelope({"kind": "content", "text": "report"}), Ev("stream.delta")
    yield Envelope({}), Ev("stream.end")


def test_stage_pipeline_aggregates_and_tags():
    seen = []

    async def on_event(envelope, event_type):
        seen.append((event_type.value, dict(envelope.data)))

    out = run(run_stage_pipeline(
        events=fake_events(),
        stage_names=("thinking", "report"),
        on_event=on_event,
    ))
    assert out == {"thinking": "why ", "report": "final report"}
    delta_stages = [d.get("stage") for et, d in seen if et == "stream.delta"]
    assert delta_stages == ["thinking", "report", "report"]


class FakeTaskIO:
    def __init__(self, cancel=False):
        self.record = {"cancel_requested": cancel} if cancel else {}

    async def get_task(self, task_id):
        return self.record

    async def update_task(self, task_id, patch):
        self.record.update(patch)


def test_stage_pipeline_cooperative_cancel():
    with pytest.raises(asyncio.CancelledError):
        run(run_stage_pipeline(
            events=fake_events(),
            stage_names=("thinking", "report"),
            task_io=FakeTaskIO(cancel=True),
            task_id="t",
        ))


def test_zero_cost_on_none():
    wrapped = zero_cost_on_none(lambda r: r["total_tokens"])
    assert wrapped(None) == 0
    assert wrapped({"total_tokens": 7}) == 7


async def failing_events():
    yield Envelope({"stages": ["thinking"]}), Ev("stream.start")
    yield Envelope({"code": "UPSTREAM_INTERRUPTED",
                    "message": "boom"}), Ev("stream.error")


def test_stage_pipeline_raises_on_stream_error_after_forwarding():
    seen = []

    async def on_event(envelope, event_type):
        seen.append(event_type.value)

    with pytest.raises(RuntimeError, match="UPSTREAM_INTERRUPTED"):
        run(run_stage_pipeline(
            events=failing_events(),
            stage_names=("thinking",),
            on_event=on_event,
        ))
    # The error event is forwarded to consumers before raising:
    # observability (SSE fan-out) is preserved on the failure path.
    assert "stream.error" in seen


async def official_content_events():
    yield Envelope({"stages": ["report"]}), Ev("stream.start")
    yield Envelope({"kind": "content", "text": "partial "}), \
        Ev("stream.delta")
    yield Envelope({"kind": "content", "text": "deltas"}), \
        Ev("stream.delta")
    yield Envelope({"name": "report",
                    "result": {"content": "official aggregate"}}), \
        Ev("stage.end")
    yield Envelope({}), Ev("stream.end")


def test_stage_pipeline_prefers_stage_end_official_content():
    out = run(run_stage_pipeline(
        events=official_content_events(),
        stage_names=("report",),
    ))
    # The runner-aggregated content wins over delta accumulation.
    assert out == {"report": "official aggregate"}