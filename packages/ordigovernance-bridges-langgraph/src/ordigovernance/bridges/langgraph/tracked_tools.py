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
    fields at validation). A single-payload capture model with
    extra="allow" is used instead, and the invocation MERGES the
    capture field with any extra top-level fields: ToolNode hands the
    model's args dict straight in ({"query": ...}), while legacy
    callers may nest the whole payload under "kwargs" — both land
    identically on the tracked call.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, create_model

from ordigovernance.api.atoms import TrackedToolSetProtocol

_PAYLOAD_FIELD = "kwargs"


def _passthrough_schema(name: str) -> type[BaseModel]:
    """Capture model: the whole tool input, direct or nested under "kwargs"."""
    return create_model(
        f"{name.title().replace('_', '')}Args",
        __config__=ConfigDict(extra="allow"),
        **{_PAYLOAD_FIELD: (dict, {})},
    )


def as_langchain_tools(tracked: TrackedToolSetProtocol,
                       specs: dict[str, dict]) -> list[StructuredTool]:
    """Adapt tracked tools to LangChain StructuredTool objects.

    Frameworks inject these into agents; the tracked set decides memo
    identity from the tool kwargs (content-addressed), so framework
    call-site variance never affects reuse.

    Real-endpoint discipline: always pass an explicit args_schema for
    tools invoked by a real model. The passthrough capture schema
    exists for deterministic tests; a real model fills parameters
    unreliably when the advertised parameter surface is a single
    opaque "kwargs" object.
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
                nested = kwargs.pop(_PAYLOAD_FIELD, None) or {}
                payload = {**kwargs, **nested}
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