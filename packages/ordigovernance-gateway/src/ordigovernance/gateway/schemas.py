"""Request/response schemas of the gateway HTTP API (pydantic v2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---- call plane ------------------------------------------------------------


class LlmChatRequest(BaseModel):
    run_id: str | None = None
    task_id: str | None = None
    client: str
    # Orchestrator-neutral defaults (D8); rename history is recorded
    # in docs/gateway-design.md. The OpenAI-compatible surface keeps
    # its own default ("openai-compat").
    purpose: str = "remote-chat"
    messages: list[dict]
    kwargs: dict[str, Any] = Field(default_factory=dict)

class LlmChatResponse(BaseModel):
    status: str
    call_id: str
    response: dict
    usage: dict | None = None


class ToolCallRequest(BaseModel):
    run_id: str | None = None
    task_id: str | None = None
    tool: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    # Reserved for engine tiers (memo reuse); the open tier always
    # executes and reports origin="executed".
    reuse: str = "always"


class ToolCallResponse(BaseModel):
    status: str
    call_id: str
    result: Any
    origin: str


# ---- task plane (Phase 3) ----------------------------------------------------


class StartRunRequest(BaseModel):
    run_id: str | None = None
    budget_max_units: int | None = None
    # Business metadata recorded on the registry entry (node-side
    # intent provenance; the gateway never interprets these fields).
    intent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunCancelResponse(BaseModel):
    run_id: str
    status: str
    cancelled_tasks: list[str] = Field(default_factory=list)


class StartRunResponse(BaseModel):
    run_id: str
    status: str


class TaskDescriptor(BaseModel):
    """Low-code task declaration: the gateway resolves impl/params
    through the registry and assembles the GovernedAgent itself."""
    task_id: str
    impl: str
    params: dict[str, Any] = Field(default_factory=dict)
    upstream: list[str] = Field(default_factory=list)
    parent_task_id: str | None = None
    tools: list[str] | None = None


class TaskAcceptedResponse(BaseModel):
    task_id: str
    accepted: bool


class TaskRecordResponse(BaseModel):
    task_id: str
    status: str | None = None
    execution_id: str | None = None
    previous_execution_ids: list[str] = Field(default_factory=list)
    result: Any = None


class FinishRunResponse(BaseModel):
    run_id: str
    final_status: str


class VocabularyEntry(BaseModel):
    name: str
    description: str = ""


class VocabularyResponse(BaseModel):
    impls: list[VocabularyEntry]
    tools: list[VocabularyEntry]
    composites: list[VocabularyEntry]


# ---- HITL plane (Phase 4) ----------------------------------------------------


class HitlActionRequest(BaseModel):
    task_id: str | None = None
    root_id: str | None = None


class ActionAcceptedResponse(BaseModel):
    action_id: str
    accepted: bool

# ---- composites (Phase 7) -------------------------------------------------


class CompositeRequest(BaseModel):
    """Drive-level background driver declaration (D10)."""
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class CompositeAcceptedResponse(BaseModel):
    composite_id: str
    accepted: bool


class CompositeChildStatus(BaseModel):
    task_id: str
    status: str | None = None
    execution_id: str | None = None


class CompositeStatusResponse(BaseModel):
    composite_id: str
    name: str
    status: str
    children: list[CompositeChildStatus] = Field(default_factory=list)
    outcome: Any = None