"""LangChainTrackedLLM: BaseChatModel shell over a TrackedLLM.

Every invocation of the wrapped model — however the framework
schedules it internally — lands as one governed call on the tracked
atom: semaphore, budget, audit and call_id idempotency apply unchanged.

Requires langchain-core at runtime; the bridge is imported explicitly
by applications that opted into the LangChain ecosystem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    convert_to_messages,
)
from langchain_core.outputs import ChatGeneration, ChatResult

if TYPE_CHECKING:
    from ordigovernance.api.atoms import TrackedLLMProtocol


_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
    "function": "tool",
}


def _to_dicts(messages: Any) -> list[dict]:
    """LangChain message objects -> plain dicts (the tracked shape)."""
    out: list[dict] = []
    for m in convert_to_messages(messages):
        if isinstance(m, BaseMessage):
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
    if message.get("tool_calls"):
        extra["tool_calls"] = message["tool_calls"]
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

    @property
    def _llm_type(self) -> str:
        return f"tracked-{self.tracked.client_name}"

    async def _agenerate(self, messages: list[BaseMessage],
                         stop=None, run_manager=None,
                         **kwargs: Any) -> ChatResult:
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