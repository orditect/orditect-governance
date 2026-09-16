"""Call-plane endpoints over the in-memory hot path."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ordigovernance.gateway.app import build_app
from ordigovernance.gateway.registry import GatewayRegistry, ToolSpec

def test_llm_chat_lands_in_ambient_with_naming_discipline(client,
                                                          auth_headers,
                                                          tmp_path):
    resp = client.post("/governed/llm-chat",
                       json={"client": "research",
                             "messages": [{"role": "user", "content": "hi"}]},
                       headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"
    assert body["call_id"].startswith("n8n-chat-n8n-call-")
    assert body["usage"]["total_tokens"] > 0

    # The audit row must carry the same call id (naming discipline).
    audit_path = None
    for path in (tmp_path / "runs").rglob("audit.ndjson"):
        audit_path = path
    assert audit_path is not None
    rows = [json.loads(x) for x in audit_path.read_text().splitlines()
            if x.strip()]
    data = rows[0].get("data", rows[0])
    assert data["event_id"] == body["call_id"]


def test_llm_chat_unknown_client_lists_vocabulary(client, auth_headers):
    resp = client.post("/governed/llm-chat",
                       json={"client": "nope", "messages": []},
                       headers=auth_headers)
    assert resp.status_code == 422
    assert "research" in resp.json()["detail"]


def test_llm_chat_unknown_task_is_404(client, auth_headers):
    resp = client.post("/governed/llm-chat",
                       json={"client": "research", "task_id": "ghost",
                             "messages": []},
                       headers=auth_headers)
    assert resp.status_code == 404


def test_auth_required(client):
    resp = client.post("/governed/llm-chat",
                       json={"client": "research", "messages": []})
    assert resp.status_code == 401


def test_tool_call_executes_and_reports_origin(settings, auth_headers):
    registry = GatewayRegistry(tools={
        "search": ToolSpec(
            factory=lambda session: _search_handler,
            resource="web_search", event_type="tool_call",
            side_effect="readonly"),
    })
    app = build_app(settings, registry=registry)
    with TestClient(app) as c:
        resp = c.post("/governed/tool-call",
                      json={"tool": "search",
                            "inputs": {"query": "ev"}},
                      headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["origin"] == "executed"
    assert body["result"]["query"] == "ev"
    assert body["call_id"].startswith("search-n8n-call-")


async def _search_handler(query: str) -> dict:
    return {"query": query, "hits": 3}


def test_tool_call_reserved_payload_key_is_422(settings, auth_headers):
    registry = GatewayRegistry(tools={
        "search": ToolSpec(
            factory=lambda session: _search_handler,
            resource="web_search", event_type="tool_call"),
    })
    app = build_app(settings, registry=registry)
    with TestClient(app) as c:
        resp = c.post("/governed/tool-call",
                      json={"tool": "search",
                            "inputs": {"params": {"q": "x"}}},
                      headers=auth_headers)
    assert resp.status_code == 422
    assert "collide" in resp.json()["detail"]


def test_healthz_reports_ambient(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["ambient"] is True