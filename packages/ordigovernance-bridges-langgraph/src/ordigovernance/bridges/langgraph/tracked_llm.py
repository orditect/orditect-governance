"""LangChainTrackedLLM: BaseChatModel shell over a TrackedLLM.

Every invocation of the wrapped model — however the framework
schedules it internally — lands as one governed call on the tracked
atom: semaphore, budget, audit and call_id idempotency apply unchanged.

Wire-shape discipline: the tracked surface speaks the OpenAI chat
shape in BOTH directions (LLMChatProtocol returns the raw response
dict; requests are OpenAI-shaped message dicts). The shell translates
LangChain message objects into OpenAI-shaped dicts — assistant
tool_calls and tool messages included — and translates the response's
OpenAI tool_calls back into LangChain ToolCall dicts, the shape
ToolNode dispatches on. bind_tools() forwards tool specs to the
tracked atom as the endpoint-native tools parameter on every call.

Requires langchain-core at runtime; the bridge is imported explicitly
by applications that opted into the LangChain ecosystem.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
    convert_to_messages,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
    "function": "tool",
}


def _tool_calls_to_openai(tool_calls: Any) -> list[dict]:
    """LangChain ToolCall dicts -> OpenAI assistant tool_calls."""
    out: list[dict] = []
    for index, call in enumerate(tool_calls or []):
        if not isinstance(call, dict):
            continue
        out.append({
            "id": call.get("id") or f"call_{index}",
            "type": "function",
            "function": {
                "name": call.get("name", ""),
                "arguments": json.dumps(call.get("args") or {},
                                        ensure_ascii=False),
            },
        })
    return out


def _to_langchain_tool_calls(raw_calls: Any) -> list[dict]:
    """OpenAI tool_calls -> LangChain ToolCall dicts ({name, args, id, type}).

    The canonical ToolCall shape carries an explicit type field;
    langchain-core normalizes entries on AIMessage construction, so
    emitting it here keeps the round-trip byte-stable. ToolCall-shaped
    dicts pass through untouched: some governed clients normalize
    upstream already.
    """
    out: list[dict] = []
    for index, call in enumerate(raw_calls or []):
        if not isinstance(call, dict):
            continue
        if "function" in call:  # OpenAI chat-completions shape
            fn = call.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {"__arg1": args}
            if not isinstance(args, dict):
                args = {"__arg1": args}
            out.append({
                "name": fn.get("name", ""),
                "args": args,
                "id": call.get("id") or f"call_{index}",
                "type": "tool_call",
            })
        elif "name" in call:
            out.append({
                "name": call["name"],
                "args": dict(call.get("args") or {}),
                "id": call.get("id") or f"call_{index}",
                "type": "tool_call",
            })
    return out


def _to_dicts(messages: Any) -> list[dict]:
    """LangChain message objects -> OpenAI-shaped dicts (tracked shape)."""
    out: list[dict] = []
    for m in convert_to_messages(messages):
        if isinstance(m, AIMessage):
            entry: dict = {"role": "assistant", "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = _tool_calls_to_openai(m.tool_calls)
            out.append(entry)
        elif isinstance(m, ToolMessage):
            out.append({
                "role": "tool",
                "content": m.content,
                "tool_call_id": m.tool_call_id,
            })
        elif isinstance(m, BaseMessage):
            out.append({
                "role": _ROLE_MAP.get(m.type, m.type),
                "content": m.content,
            })
        elif isinstance(m, dict):
            out.append(dict(m))
        else:  # (role, content) tuple form
            out.append({"role": m[0], "content": m[1]})
    return out


def _to_ai_message(response: dict) -> AIMessage:
    """Raw OpenAI-shaped response dict -> LangChain AIMessage."""
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    extra: dict[str, Any] = {}
    tool_calls = _to_langchain_tool_calls(message.get("tool_calls"))
    if tool_calls:
        extra["tool_calls"] = tool_calls
    usage = response.get("usage")
    if usage:
        extra["usage_metadata"] = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
    return AIMessage(content=message.get("content") or "", **extra)


class LangChainTrackedLLM(BaseChatModel):
    """A BaseChatModel whose every call is a governed tracked call.

    The framework sees an ordinary chat model; the governance plane
    sees one audited, budgeted, idempotent call per invocation.
    """

    tracked: Any  # TrackedLLMProtocol (typed as Any: pydantic field discipline)
    _openai_tools: list[dict] | None = PrivateAttr(default=None)
    _tool_choice: Any = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return f"tracked-{self.tracked.client_name}"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None,
                   **kwargs: Any) -> "LangChainTrackedLLM":
        """Bind tool specs as OpenAI tool parameters.

        langgraph/deepagents bind the tool list onto the model once at
        graph build; the specs ride along on every tracked call as the
        endpoint-native tools parameter, so the model's tool-calling
        channel stays real (never prompt-simulated). Returns a clone:
        the unbound model never carries tool specs.
        """
        clone = self.model_copy()
        clone._openai_tools = [convert_to_openai_tool(t) for t in tools]
        clone._tool_choice = tool_choice
        return clone

    async def _agenerate(self, messages: list[BaseMessage],
                         stop=None, run_manager=None,
                         **kwargs: Any) -> ChatResult:
        if self._openai_tools is not None:
            kwargs.setdefault("tools", self._openai_tools)
        if self._tool_choice is not None:
            kwargs.setdefault("tool_choice", self._tool_choice)
        response = await self.tracked.complete(
            _to_dicts(messages), **kwargs
        )
        return ChatResult(
            generations=[ChatGeneration(message=_to_ai_message(response))]
        )

    def _generate(self, messages: list[BaseMessage],
                  stop=None, run_manager=None,
                  **kwargs: Any) -> ChatResult:
        raise NotImplementedError(
            "LangChainTrackedLLM is async-only: use ainvoke/astream "
            "paths (governed calls are awaitable by construction)"
        )