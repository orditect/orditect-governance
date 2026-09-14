"""as_langchain_tools: StructuredTool shells over a TrackedToolSet.

specs carry the framework-facing facts (description, optional args
schema); invocation goes through the tracked set, so every tool call
is memoized, audited and budgeted like any other A-class atom.

specs shape:
    {name: {"description": str, "args_schema": BaseModel | None}}

Schema discipline:
  - WITH an explicit args_schema, langchain validates and forwards
    each declared field as a kwarg — the tracked call receives them
    verbatim.
  - WITHOUT one, langchain would infer a schema from the coroutine
    signature and swallow extra keys (pydantic v2 drops undeclared
    fields at validation). A single-payload capture model is used
    instead: the framework hands the WHOLE tool-input dict over as one
    value, which is then unpacked into the tracked call.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model

from ordigovernance.api.atoms import TrackedToolSetProtocol

_PAYLOAD_FIELD = "kwargs"


def _passthrough_schema(name: str) -> type[BaseModel]:
    """Single-dict capture model: the whole tool input as one value."""
    return create_model(
        f"{name.title().replace('_', '')}Args",
        **{_PAYLOAD_FIELD: (dict, ...)},
    )


def as_langchain_tools(tracked: TrackedToolSetProtocol,
                       specs: dict[str, dict]) -> list[StructuredTool]:
    """Adapt tracked tools to LangChain StructuredTool objects.

    Frameworks inject these into agents; the tracked set decides memo
    identity from the tool kwargs (content-addressed), so framework
    call-site variance never affects reuse.
    """
    tools: list[StructuredTool] = []
    for name, spec in specs.items():
        explicit_schema = spec.get("args_schema")

        if explicit_schema is not None:
            async def _invoke(_name=name, **kwargs: Any) -> Any:
                return await tracked.call(
                    _name, inputs=dict(kwargs), **kwargs)
            schema = explicit_schema
        else:
            async def _invoke(_name=name, **kwargs: Any) -> Any:
                payload = kwargs.get(_PAYLOAD_FIELD, kwargs)
                return await tracked.call(
                    _name, inputs=dict(payload), **dict(payload))
            schema = _passthrough_schema(name)

        tools.append(StructuredTool.from_function(
            func=None,
            coroutine=_invoke,
            name=name,
            description=spec.get("description", name),
            args_schema=schema,
        ))
    return tools