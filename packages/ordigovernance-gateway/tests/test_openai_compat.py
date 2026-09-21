"""OpenAI-compatible surface (/v1/*): envelope-over-governance contract.

Locks the D18 wire semantics: the strict /v1/models shape (n8n's
OpenAI credential test and model dropdown both read it), the
model->client mapping, opaque kwargs transport, the attribution matrix
(headers, the @active sentinel, the D2 ambient fallback), OpenAI error
envelopes, streaming chunk envelopes with tool_calls passthrough, and
the fail-loudly shape gate on non-OpenAI-shaped clients.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.config import GatewaySettings

TERMINAL_WORDS = frozenset({"succeeded", "failed", "cancelled"})


def _audit_event_ids(trace_dir) -> list[str]:
    path = trace_dir / "audit.ndjson"
    if not path.is_file():
        return []
    out: list[str] = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out.append(row.get("data", row).get("event_id", ""))
    return out


def _start_run(client: TestClient, headers: dict, run_id: str) -> str:
    resp = client.post("/runs", json={"run_id": run_id},
                       headers=headers)
    assert resp.status_code == 201
    return resp.json()["run_id"]


def _wait_terminal(client: TestClient, run_id: str, task_id: str,
                   headers: dict, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/runs/{run_id}/tasks/{task_id}",
                          headers=headers)
        assert resp.status_code == 200
        record = resp.json()
        if record["status"] in TERMINAL_WORDS:
            return record
        time.sleep(0.05)
    raise AssertionError(f"{task_id} never settled")


# ---- shape gate + models -----------------------------------------------------


def test_governed_and_compat_shapes_match(client, auth_headers):
    """Shape gate: the compat surface is an envelope swap over the
    governed path -- both must return the same choices/usage."""
    gov = client.post("/governed/llm-chat",
                      json={"client": "research",
                            "messages": [{"role": "user", "content": "hi"}]},
                      headers=auth_headers).json()
    compat = client.post("/v1/chat/completions",
                         json={"model": "research",
                               "messages": [{"role": "user",
                                             "content": "hi"}]},
                         headers=auth_headers).json()
    assert compat["choices"] == gov["response"]["choices"]
    assert compat["usage"] == gov["usage"]
    assert compat["object"] == "chat.completion"
    assert compat["model"] == "research"
    assert "created" in compat
    # Naming discipline: the default purpose segment.
    assert compat["id"].startswith("openai-compat-")


def test_models_endpoint_openai_shape(client, auth_headers):
    resp = client.get("/v1/models", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["research"]
    for entry in body["data"]:
        assert entry["object"] == "model"
        assert "created" in entry
        assert "owned_by" in entry


def test_models_requires_auth(client):
    assert client.get("/v1/models").status_code == 401


def test_unknown_model_error_lists_vocabulary(client, auth_headers):
    resp = client.post("/v1/chat/completions",
                       json={"model": "nope", "messages": []},
                       headers=auth_headers)
    assert resp.status_code == 422
    body = resp.json()
    # OpenAI error envelope (openai-js parses this shape).
    assert "research" in body["error"]["message"]
    assert body["error"]["type"] == "invalid_request_error"


# ---- opaque kwargs transport ---------------------------------------------------


class _RecordingClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def chat(self, messages, *, call_id, **kwargs):
        self.calls.append({"messages": messages, "call_id": call_id,
                           "kwargs": dict(kwargs)})
        return {"choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 1}}


@pytest.fixture
def recording_client(tmp_path):
    recorder = _RecordingClient()

    async def _clients(hooks: dict) -> dict:
        return {"research": recorder}

    settings = GatewaySettings(
        redis_url=None, trace_root=tmp_path / "runs",
        auth_token="test-token", make_clients=_clients)
    app = build_app(settings)
    with TestClient(app) as c:
        yield c, recorder


def test_request_kwargs_transported_opaquely(recording_client, auth_headers):
    c, recorder = recording_client
    resp = c.post("/v1/chat/completions", json={
        "model": "research",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function",
                   "function": {"name": "search"}}],
        "tool_choice": "auto",
        "temperature": 0.2,
        "stop": ["###"],
        "user": "end-user-123",
    }, headers=auth_headers)
    assert resp.status_code == 200
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    for consumed in ("model", "messages", "stream", "stream_options"):
        assert consumed not in call["kwargs"]
    assert call["kwargs"]["tools"][0]["function"]["name"] == "search"
    assert call["kwargs"]["tool_choice"] == "auto"
    assert call["kwargs"]["temperature"] == 0.2
    assert call["kwargs"]["stop"] == ["###"]
    assert call["kwargs"]["user"] == "end-user-123"


# ---- attribution matrix --------------------------------------------------------


def test_attribution_absent_header_routes_to_ambient(client, auth_headers,
                                                      settings):
    resp = client.post("/v1/chat/completions", json={
        "model": "research",
        "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_headers)
    assert resp.status_code == 200
    ids = _audit_event_ids(settings.trace_root / "ambient" / "trace")
    assert any(i.startswith("openai-compat-") for i in ids)


def test_attribution_explicit_ambient_header(client, auth_headers,
                                              settings):
    resp = client.post("/v1/chat/completions", json={
        "model": "research", "messages": []},
        headers={**auth_headers, "X-Governance-Run-Id": "ambient"})
    assert resp.status_code == 200
    ids = _audit_event_ids(settings.trace_root / "ambient" / "trace")
    assert any(i.startswith("openai-compat-") for i in ids)


def test_attribution_explicit_run_id(client, auth_headers, settings):
    run_id = _start_run(client, auth_headers, "run-explicit")
    resp = client.post("/v1/chat/completions", json={
        "model": "research",
        "messages": [{"role": "user", "content": "hi"}]},
        headers={**auth_headers, "X-Governance-Run-Id": run_id})
    assert resp.status_code == 200
    ids = _audit_event_ids(settings.trace_root / run_id / "trace")
    assert any(i.startswith("openai-compat-") for i in ids)


def test_attribution_finished_run_is_404(client, auth_headers):
    run_id = _start_run(client, auth_headers, "run-done")
    resp = client.post(f"/runs/{run_id}/finish", headers=auth_headers)
    assert resp.status_code == 200
    resp = client.post("/v1/chat/completions", json={
        "model": "research", "messages": []},
        headers={**auth_headers, "X-Governance-Run-Id": run_id})
    assert resp.status_code == 404
    assert "unknown or inactive run" in resp.json()["error"]["message"]


def test_attribution_active_run_sentinel(client, auth_headers, settings):
    """@active: a credential-static header with runtime-dynamic
    resolution. With an active run the call's evidence lands in the
    RUN's bundle; after the run finishes the same static header
    degrades to ambient instead of 404ing on every later execution."""
    run_id = _start_run(client, auth_headers, "run-sentinel")

    resp = client.post("/v1/chat/completions", json={
        "model": "research",
        "messages": [{"role": "user", "content": "hi"}]},
        headers={**auth_headers, "X-Governance-Run-Id": "@active"})
    assert resp.status_code == 200
    run_ids = _audit_event_ids(settings.trace_root / run_id / "trace")
    assert any(i.startswith("openai-compat-") for i in run_ids)
    ambient_ids = _audit_event_ids(settings.trace_root / "ambient" / "trace")
    assert not any(i.startswith("openai-compat-") for i in ambient_ids)

    # Run turnover: the sentinel must survive it (ambient fallback).
    resp = client.post(f"/runs/{run_id}/finish", headers=auth_headers)
    assert resp.status_code == 200
    resp = client.post("/v1/chat/completions", json={
        "model": "research",
        "messages": [{"role": "user", "content": "hi"}]},
        headers={**auth_headers, "X-Governance-Run-Id": "@active"})
    assert resp.status_code == 200
    ambient_ids = _audit_event_ids(settings.trace_root / "ambient" / "trace")
    assert sum(1 for i in ambient_ids
               if i.startswith("openai-compat-")) == 1


# ---- task / purpose attribution -------------------------------------------------


def test_task_header_reuses_hot_record_eid(registry_client, auth_headers):
    run_id = _start_run(registry_client, auth_headers, "run-task")
    registry_client.post(f"/runs/{run_id}/tasks", json={
        "task_id": "task-a", "impl": "echo", "params": {}},
        headers=auth_headers)
    record = _wait_terminal(registry_client, run_id, "task-a",
                            auth_headers)
    resp = registry_client.post("/v1/chat/completions", json={
        "model": "research", "messages": []},
        headers={**auth_headers, "X-Governance-Run-Id": run_id,
                 "X-Governance-Task-Id": "task-a"})
    assert resp.status_code == 200
    # D8: the call_id names the task's hot-record generation.
    assert (f"openai-compat-task-a-{record['execution_id']}-"
            in resp.json()["id"])


def test_task_header_unknown_task_is_404(client, auth_headers):
    run_id = _start_run(client, auth_headers, "run-ghost-task")
    resp = client.post("/v1/chat/completions", json={
        "model": "research", "messages": []},
        headers={**auth_headers, "X-Governance-Run-Id": run_id,
                 "X-Governance-Task-Id": "ghost"})
    assert resp.status_code == 404


def test_task_header_omitted_mints_ephemeral_identity(client, auth_headers):
    resp = client.post("/v1/chat/completions", json={
        "model": "research", "messages": []},
        headers=auth_headers)
    assert resp.status_code == 200
    assert "openai-compat-remote-call-" in resp.json()["id"]


def test_purpose_header_overrides_default(client, auth_headers):
    resp = client.post("/v1/chat/completions", json={
        "model": "research", "messages": []},
        headers={**auth_headers, "X-Governance-Purpose": "n8n-agent"})
    assert resp.status_code == 200
    assert resp.json()["id"].startswith("n8n-agent-")


# ---- streaming -----------------------------------------------------------------


class _CompatStreamingClient:
    """Yields OpenAI-shaped deltas incl. tool_calls and a usage tail."""

    async def chat(self, messages, *, call_id, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}

    async def stream(self, messages, *, call_id, **kwargs):
        # The stream_options translation must reach the client.
        assert kwargs.get("include_usage") is True
        yield {"choices": [{"delta": {"content": "hel"}}]}
        yield {"choices": [{"delta": {
            "tool_calls": [{"index": 0, "id": "call_1",
                            "type": "function",
                            "function": {"name": "search",
                                         "arguments": "{}"}}]}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        yield {"choices": [], "usage": {"prompt_tokens": 2,
                                        "completion_tokens": 2,
                                        "total_tokens": 4}}


@pytest.fixture
def streaming_compat_client(tmp_path):
    async def _clients(hooks: dict) -> dict:
        return {"research": _CompatStreamingClient()}

    settings = GatewaySettings(
        redis_url=None, trace_root=tmp_path / "runs",
        auth_token="test-token", make_clients=_clients)
    app = build_app(settings)
    with TestClient(app) as c:
        yield c


def _frames_from_stream(client: TestClient, path: str, json_body: dict,
                        headers: dict) -> list:
    frames: list = []
    with client.stream("POST", path, json=json_body,
                       headers=headers) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            try:
                frames.append(json.loads(payload))
            except json.JSONDecodeError:
                frames.append(payload)
    return frames


def test_stream_envelopes_and_tool_calls_passthrough(
        streaming_compat_client, auth_headers):
    frames = _frames_from_stream(
        streaming_compat_client, "/v1/chat/completions", {
            "model": "research",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }, auth_headers)
    # [DONE] terminator.
    assert frames[-1] == "[DONE]"
    chunks = [f for f in frames if isinstance(f, dict)]
    assert not [f for f in chunks if "error" in f]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["id"].startswith("openai-compat-")
    # Text delta.
    assert chunks[0]["choices"][0]["delta"]["content"] == "hel"
    # tool_calls delta passthrough (streaming tool-calling rounds).
    assert chunks[1]["choices"][0]["delta"]["tool_calls"][0][
        "function"]["name"] == "search"
    # finish_reason passthrough.
    assert chunks[2]["choices"][0]["finish_reason"] == "tool_calls"
    # Final chunk: stop marker + usage from the tail chunk.
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"]["total_tokens"] == 4


def test_stream_unsupported_model_errors_clearly(recording_client,
                                                  auth_headers):
    c, _ = recording_client
    # The recording client exposes chat() only.
    resp = c.post("/v1/chat/completions", json={
        "model": "research",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }, headers=auth_headers)
    assert resp.status_code == 422
    assert "does not expose stream" in resp.json()["error"]["message"]


# ---- fail-loudly shape gate -----------------------------------------------------


class _ShapedWrongClient:
    async def chat(self, messages, *, call_id, **kwargs):
        return {"text": "not openai shaped"}  # no "choices"


def test_non_openai_shaped_client_fails_loudly(tmp_path, auth_headers):
    async def _clients(hooks: dict) -> dict:
        return {"research": _ShapedWrongClient()}

    settings = GatewaySettings(
        redis_url=None, trace_root=tmp_path / "runs",
        auth_token="test-token", make_clients=_clients)
    app = build_app(settings)
    with TestClient(app) as c:
        resp = c.post("/v1/chat/completions", json={
            "model": "research",
            "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers)
        assert resp.status_code == 502
        body = resp.json()
        assert "choices" in body["error"]["message"]
        assert "GATEWAY_MODEL_CLIENTS" in body["error"]["message"]