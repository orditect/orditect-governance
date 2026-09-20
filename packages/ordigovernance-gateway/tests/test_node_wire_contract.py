"""Node wire-contract mirror: the exact field vocabulary the n8n nodes send.

The n8n node package builds request bodies by hand; the gateway schema
drops unknown fields silently (pydantic default), so a drift on either
side fails one node late and invisibly -- the exact defect the Run/Tool
nodes shipped with (decorative client/purpose/metadata parameters and
an unreachable budget cap, every node test green because the mocks
encoded the same wrong contract). This suite pins the wire contract
from the gateway side:

  - every node-facing request schema's FIELD SET, extracted from the
    app's own openapi (no hand-maintained copies to drift);
  - the intent/metadata provenance fields landing on the registry entry;
  - the cancel escape hatch (wedged-run recovery without a restart);
  - the SSE frame vocabulary of the streaming endpoint.

When this file changes, the n8n-side node bodies, the node contract
tests and docs/gateway-contract.md must change in the same commit.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.config import GatewaySettings


def _request_schema_fields(client: TestClient, schema: str) -> set[str]:
    """Field names of one request schema, from the app's own openapi."""
    spec = client.get("/openapi.json").json()
    return set(spec["components"]["schemas"][schema]["properties"])


class TestSchemaFieldSets:
    """The exact body vocabulary the node bodies may carry.

    These assertions are the drift guard: a schema rename/removal here
    means the n8n nodes silently drop the field (pydantic ignores
    unknowns), so the gateway build fails FIRST and names the field.
    """

    def test_start_run_request(self, client):
        assert _request_schema_fields(client, "StartRunRequest") == {
            "run_id", "budget_max_units", "intent", "metadata"}

    def test_tool_call_request(self, client):
        assert _request_schema_fields(client, "ToolCallRequest") == {
            "run_id", "task_id", "tool", "inputs", "reuse"}

    def test_task_descriptor(self, client):
        assert _request_schema_fields(client, "TaskDescriptor") == {
            "task_id", "impl", "params", "upstream", "parent_task_id",
            "tools"}

    def test_llm_chat_request(self, client):
        assert _request_schema_fields(client, "LlmChatRequest") == {
            "run_id", "task_id", "client", "purpose", "messages",
            "kwargs"}


def test_start_run_records_intent_and_metadata(registry_client,
                                                auth_headers):
    """intent/metadata ride the registry entry verbatim (provenance)."""
    resp = registry_client.post("/runs", json={
        "run_id": "run-meta", "intent": "fan-out demo",
        "metadata": {"origin": "n8n"}}, headers=auth_headers)
    assert resp.status_code == 201
    entry = registry_client.get("/runs/run-meta",
                                headers=auth_headers).json()
    assert entry["intent"] == "fan-out demo"
    assert entry["params"] == {"origin": "n8n"}


def test_cancel_run_settles_tasks_and_releases_single_active(
        registry_client, auth_headers):
    """The wedged-run escape hatch: cancel settles running tasks and
    closes the run WITHOUT a gateway restart (which would kill the
    dispatcher, pitfalls 16.9)."""
    resp = registry_client.post("/runs", json={"run_id": "run-wedged"},
                                headers=auth_headers)
    assert resp.status_code == 201
    registry_client.post("/runs/run-wedged/tasks", json={
        "task_id": "slow-task", "impl": "slow",
        "params": {"delay": 0.5}}, headers=auth_headers)

    # Wait until the task is actually RUNNING (a too-early cancel would
    # target a pending task the sink never sees).
    deadline = time.time() + 10.0
    record = {}
    while time.time() < deadline:
        record = registry_client.get(
            "/runs/run-wedged/tasks/slow-task",
            headers=auth_headers).json()
        if record.get("status") == "running":
            break
        time.sleep(0.05)
    assert record.get("status") == "running"

    resp = registry_client.post("/runs/run-wedged/cancel",
                                headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["cancelled_tasks"] == ["slow-task"]

    # The run is closed (hot reads 404) and the single-active guard is
    # released: a new run may start immediately.
    assert registry_client.get(
        "/runs/run-wedged/tasks/slow-task",
        headers=auth_headers).status_code == 404
    resp = registry_client.post("/runs", json={"run_id": "run-after"},
                                headers=auth_headers)
    assert resp.status_code == 201


class _StreamingScriptedClient:
    """Deterministic client exposing both chat() and stream().

    stream() yields plain {"delta": str} chunks plus the OpenAI-shaped
    usage tail chunk, exercising the endpoint's chunk-field tolerance.
    """

    async def chat(self, messages, *, call_id, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}

    async def stream(self, messages, *, call_id, **kwargs):
        for token in ("hel", "lo"):
            yield {"delta": token}
        yield {"choices": [], "usage": {"prompt_tokens": 2,
                                        "completion_tokens": 2,
                                        "total_tokens": 4}}


@pytest.fixture
def stream_client(tmp_path):
    async def _clients(hooks: dict) -> dict:
        return {"research": _StreamingScriptedClient()}

    settings = GatewaySettings(
        redis_url=None,
        trace_root=tmp_path / "runs",
        auth_token="test-token",
        make_clients=_clients,
    )
    app = build_app(settings)
    with TestClient(app) as c:
        yield c


def _sse_frames(body: str) -> list[dict]:
    frames = []
    for line in body.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[len("data: "):]))
    return frames

def _sse_frames_from_stream(client: TestClient, path: str,
                            json_body: dict,
                            headers: dict) -> list[dict]:
    """Consume a StreamingResponse fully, collecting SSE frames.

    TestClient wraps StreamingResponse in a way that defers body
    generation; reading resp.text after a plain post() can observe an
    empty body. Driving the request through client.stream(...) forces
    full consumption before parsing.
    """
    frames: list[dict] = []
    with client.stream("POST", path, json=json_body,
                       headers=headers) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: "):
                frames.append(json.loads(line[len("data: "):]))
    return frames

def test_llm_chat_stream_yields_the_frame_vocabulary(
        stream_client, auth_headers):
    with TestClient(stream_client.app) as c:
        frames = _sse_frames_from_stream(
            c, "/governed/llm-chat-stream",
            {"client": "research",
             "messages": [{"role": "user", "content": "hi"}]},
            auth_headers)

        assert [f["type"] for f in frames] == [
            "delta", "delta", "done"]
        assert "".join(f["text"] for f in frames
                       if f["type"] == "delta") == "hello"
        done = frames[-1]
        # Naming discipline: purpose defaults to n8n-chat, the ephemeral
        # identity starts with n8n-call-, the seq sits above the agent band.
        assert done["call_id"].startswith("n8n-chat-n8n-call-")
        assert done["usage"]["total_tokens"] == 4


def test_llm_chat_stream_rejects_non_streaming_client(client,
                                                       auth_headers):
    # The default fixture's scripted client exposes chat() only.
    resp = client.post("/governed/llm-chat-stream", json={
        "client": "research", "messages": []}, headers=auth_headers)
    assert resp.status_code == 422
    assert "does not expose stream" in resp.json()["detail"]


def test_llm_chat_stream_unknown_client_lists_vocabulary(
        stream_client, auth_headers):
    resp = stream_client.post("/governed/llm-chat-stream", json={
        "client": "nope", "messages": []}, headers=auth_headers)
    assert resp.status_code == 422
    assert "research" in resp.json()["detail"]

class _SourceChunkLike:
    """Duck-typed stand-in for the orditect framework's SourceChunk."""

    def __init__(self, text: str | None, thinking: str | None) -> None:
        self.text = text
        self.thinking = thinking


class _SourceChunkClient(_StreamingScriptedClient):
    """Streams SourceChunk objects, including the terminal marker."""
    async def stream(self, messages, *, call_id, **kwargs):
        yield _SourceChunkLike(None, "thinking through it")
        yield _SourceChunkLike("real text ", None)
        yield _SourceChunkLike("tail", None)
        # Terminal marker: both fields None, finish=True -> no frame.
        yield _SourceChunkLike(None, None)

def test_llm_chat_stream_extracts_source_chunk_objects(
        tmp_path, auth_headers):
    async def _clients(hooks: dict) -> dict:
        return {"research": _SourceChunkClient()}

    settings = GatewaySettings(
        redis_url=None,
        trace_root=tmp_path / "runs",
        auth_token="test-token",
        make_clients=_clients,
    )
    app = build_app(settings)
    with TestClient(app) as c:
        frames = _sse_frames_from_stream(
            c, "/governed/llm-chat-stream",
            {"client": "research",
             "messages": [{"role": "user", "content": "hi"}]},
            auth_headers)
        deltas = [f for f in frames if f["type"] == "delta"]
        # Regression locks: (1) field extraction, never the repr; (2)
        # thinking maps to the reasoning channel; (3) the terminal
        # marker chunk emits NO frame (3 deltas, not 4).
        assert len(deltas) == 3
        assert [f["text"] for f in deltas] == ["", "real text ", "tail"]
        assert [f["reasoning"] for f in deltas] == [
            "thinking through it", "", ""]
        assert not any("SourceChunk(" in f.get("text", "")
                       for f in deltas)
        done = [f for f in frames if f["type"] == "done"]
        assert done and done[-1]["call_id"].startswith(
            "n8n-chat-n8n-call-")