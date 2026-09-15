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
from typing import Any, AsyncIterator

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
    convert_to_messages,
)
from langchain_core.outputs import (
    ChatGeneration,
    ChatGenerationChunk,
    ChatResult,
)
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


def _raw_tool_calls(message: AIMessage) -> Any:
    """Tool calls of one assistant message, in whichever shape they use.

    message.tool_calls is the normalized LangChain shape; middleware
    chains (deepagents) may leave the raw OpenAI entries only under
    additional_kwargs["tool_calls"]. Both must reach the endpoint:
    dropping them makes the next round's history reference
    tool_call_ids the assistant message never declared, and the
    endpoint rejects the request with a 400.
    """
    if message.tool_calls:
        return message.tool_calls
    return (message.additional_kwargs or {}).get("tool_calls") or None


def _chunk_text(chunk: Any) -> str:
    """Best-effort text extraction from one raw stream chunk.

    The tracked surface forwards chunks verbatim from the underlying
    client: the scripted client emits {"delta": str}, OpenAI-shaped
    clients emit {"choices": [{"delta": {"content": str}}]}.
    """
    if isinstance(chunk, dict):
        delta = chunk.get("delta")
        if isinstance(delta, str):
            return delta
        if isinstance(delta, dict):
            return str(delta.get("content") or "")
        choices = chunk.get("choices") or []
        if choices:
            inner = (choices[0] or {}).get("delta") or {}
            return str(inner.get("content") or "")
        return ""
    return str(chunk)

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
    """LangChain message objects -> OpenAI-shaped dicts (tracked shape).

    convert_to_messages normalizes every accepted input (dicts,
    (role, content) tuples, message objects) before the loop, so each
    branch below sees a real BaseMessage.
    """
    out: list[dict] = []
    for m in convert_to_messages(messages):
        if isinstance(m, AIMessage):
            entry: dict = {"role": "assistant", "content": m.content}
            raw_calls = _raw_tool_calls(m)
            if raw_calls:
                # Already wire-shaped entries pass through untouched;
                # normalized ToolCall dicts are translated back.
                entry["tool_calls"] = (
                    _tool_calls_to_openai(raw_calls)
                    if isinstance(raw_calls[0], dict)
                    and "name" in raw_calls[0]
                    else list(raw_calls)
                )
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
    _bind_kwargs: dict | None = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return f"tracked-{self.tracked.client_name}"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None,
                   **kwargs: Any) -> "LangChainTrackedLLM":
        """Bind tool specs as OpenAI tool parameters.

        langgraph/deepagents bind the tool list onto the model once at
        graph build; the specs ride along on every tracked call as the
        endpoint-native tools parameter, so the model's tool-calling
        channel stays real (never prompt-simulated). Extra bind
        options (parallel_tool_calls, ...) are stored and forwarded
        verbatim on every call. Returns a clone: the unbound model
        never carries tool specs.
        """
        clone = self.model_copy()
        clone._openai_tools = [convert_to_openai_tool(t) for t in tools]
        clone._tool_choice = tool_choice
        clone._bind_kwargs = dict(kwargs) or None
        return clone

    def _merged_call_kwargs(self, stop: Any, kwargs: dict) -> dict:
        """Per-call kwargs: bind-time options, tool specs, stop words."""
        out = dict(kwargs)
        if stop is not None:
            out.setdefault("stop", list(stop))
        if self._openai_tools is not None:
            out.setdefault("tools", self._openai_tools)
        if self._tool_choice is not None:
            out.setdefault("tool_choice", self._tool_choice)
        if self._bind_kwargs:
            out = {**self._bind_kwargs, **out}
        return out

    async def _agenerate(self, messages: list[BaseMessage],
                         stop=None, run_manager=None,
                         **kwargs: Any) -> ChatResult:
        response = await self.tracked.complete(
            _to_dicts(messages), **self._merged_call_kwargs(stop, kwargs)
        )
        return ChatResult(
            generations=[ChatGeneration(message=_to_ai_message(response))]
        )

    async def _astream(self, messages: list[BaseMessage],
                       stop=None, run_manager=None,
                       **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        """Token-level streaming through the tracked atom.

        Without this override BaseChatModel.astream falls back to one
        monolithic chunk, degrading astream_events consumers (the
        deepagents UI stream) to a single block.
        """
        async for chunk in self.tracked.stream(
                _to_dicts(messages),
                **self._merged_call_kwargs(stop, kwargs)):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=_chunk_text(chunk)))

    def _generate(self, messages: list[BaseMessage],
                  stop=None, run_manager=None,
                  **kwargs: Any) -> ChatResult:
        raise NotImplementedError(
            "LangChainTrackedLLM is async-only: use ainvoke/astream "
            "paths (governed calls are awaitable by construction)"
        )