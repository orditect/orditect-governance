"""AgentContext: the governed surface handed to agent implementations.

An agent decomposes into a sequence of atomic calls of exactly two
kinds:

  A-class: non-LLM world reads (tools / skills / memory / retrieval),
           issued through ctx.tools or wrapped via ctx.memoize;
  B-class: LLM interactions, issued through clients from ctx.llm(name).

Composition, ordering, conditionals, and iteration over these atoms are
business logic and belong to the bridge implementation; the context
only guarantees that every atom is governed and that memo/archive
behavior is automatic and identical regardless of how the agent is
composed.

Governance vocabulary (eid/scope/seq/origin ids) is assembled inside
this facade and never exposed to the implementation.

Engine plug-in: this runtime ships mechanism-direct defaults
(passthrough memo and passthrough policy routing). Engines with
richer semantics (memo reuse across generations, replay policy
routing) implement the api MemoLayerProtocol / PolicyResolverProtocol
and are injected at assembly time.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ordigovernance.api.context import MemoLayerProtocol, PolicyResolverProtocol
from ordigovernance.api.llm import LLMChatProtocol, LLMStreamProtocol
from ordigovernance.api.memo import MemoBackend
from ordigovernance.api.naming import make_call_id
from ordigovernance.api.side_effect import STUB, CallClass, SideEffect
from ordigovernance.api.task import GenerationMeta
from ordigovernance.runtime.tools.governed_tools import GovernedToolSet
class PassthroughResolver:
    """Mechanism-direct policy resolver: no replay routing in force.

    Every call site's declared reuse stays in force; external side
    effects fire normally (mainline behavior). This is the default
    when no engine resolver is injected.
    """

    @property
    def active(self) -> bool:
        return False

    @property
    def table(self) -> dict:
        return {}

    def resolve(
        self,
        purpose: str,
        category: CallClass,
        *,
        side_effect: SideEffect = SideEffect.READONLY,
        declared: str = "always",
    ) -> str:
        if category in (CallClass.PRODUCER, CallClass.INTERNAL):
            return "never"
        return declared


class AgentContext:
    """Per-generation facade: tools, llm registry, memoize, archive."""

    def __init__(
            self,
            meta: GenerationMeta,
            *,
            tools: GovernedToolSet | None,
            llms: dict[str, LLMChatProtocol],
            archive_backend: MemoBackend | None,
            memo_scope: str,
            llm_params: dict | None = None,
            memo_layer: MemoLayerProtocol | None = None,
            policy_resolver: PolicyResolverProtocol | None = None,
    ) -> None:
        self._meta = meta
        self._previous_status = meta.previous_status
        self._tools = tools
        self._llms = dict(llms)
        self._archive_backend = archive_backend
        self._llm_params = dict(llm_params or {})
        self._resolver = policy_resolver or PassthroughResolver()
        self._memo = memo_layer   # None -> memoize passes through
        self._memo_scope = memo_scope
        self.origins: dict[str, str | list[str]] = {}
    @property
    def meta(self) -> GenerationMeta:
        return self._meta

    @property
    def tools(self) -> GovernedToolSet:
        """A-class governed tools (business-registered handlers)."""
        if self._tools is None:
            raise RuntimeError(
                f"{self._meta.task_id}: governed tools not wired"
            )
        return self._tools

    def llm(self, name: str) -> LLMChatProtocol:
        """B-class client from the registry; names are business-owned."""
        try:
            return self._llms[name]
        except KeyError:
            raise KeyError(
                f"unknown llm client {name!r}; registered: {sorted(self._llms)}"
            ) from None

    def call_id(self, purpose: str, seq: int | None = None) -> str:
        """Current-generation call id for direct ctx.tools/ctx.llm calls."""
        return make_call_id(purpose, self._meta.task_id, self._meta.eid,
                            seq=seq)

    async def tool_call(self, name: str, *args: Any, purpose: str,
                        seq: int, params: dict, **kwargs: Any) -> Any:
        """Direct governed tool call (no memo), current-generation id."""
        return await self.tools.call(
            name, *args, call_id=self.call_id(purpose, seq),
            params=params, **kwargs,
        )

    async def llm_call(self, client: str, purpose: str, seq: int,
                       messages: list[dict], **kwargs: Any) -> dict:
        """Direct governed LLM call (no memo), current-generation id.

        llm_params from the replay channel (e.g. temperature/seed) are
        merged over the call kwargs: they belong to the experiment
        declaration, not to the business call site. The merge order is
        part of the api contract — engine tiers must preserve
        {**call_kwargs, **llm_params}.
        """
        merged = {**kwargs, **self._llm_params}
        return await self.llm(client).chat(
            messages=messages, call_id=self.call_id(purpose, seq), **merged
        )

    async def llm_stream(self, client: str, purpose: str, seq: int,
                         messages: list[dict], **kwargs: Any):
        """Direct governed LLM streaming call, current-generation id.

        Yields chunks as they arrive. The stream is NEVER memoized:
        chunk sequences have no stable memo identity. Cancellation
        tokens may be passed through kwargs for cooperative cancel.
        """
        target = self.llm(client)
        if not isinstance(target, LLMStreamProtocol):
            raise RuntimeError(
                f"llm client {client!r} does not expose stream()"
            )
        async for chunk in target.stream(
                messages=messages, call_id=self.call_id(purpose, seq),
                **{**kwargs, **self._llm_params},
        ):
            yield chunk

    def tool_side_effect(self, name: str) -> SideEffect:
        """Registered side-effect tag of one tool (readonly default)."""
        if self._tools is None:
            return SideEffect.READONLY
        getter = getattr(self._tools, "side_effect_of", None)
        if getter is None:
            return SideEffect.READONLY
        return getter(name)

    @property
    def policy_resolver(self) -> PolicyResolverProtocol:
        """The generation's policy resolver (shared by all call paths)."""
        return self._resolver

    @property
    def llm_params(self) -> dict:
        """Read-only view of the replay-channel sampling params.

        Additive public surface: engine components building derived
        contexts (e.g. nested intervals) inherit the experiment's
        declared sampling params. Returns a copy; mutation never
        reaches the generation's wiring.
        """
        return dict(self._llm_params)

    # ---- public read surface for engine components -------------------------

    @property
    def memo_scope(self) -> str:
        """The generation's memo scope (the run-scoped namespace)."""
        return self._memo_scope

    @property
    def memo_backend(self) -> MemoBackend | None:
        """The backend backing the memo/archive content layer.

        Engine components (nested intervals, engine tracked atoms)
        build their own memo layers over this backend; None in
        ungoverned setups.
        """
        return self._archive_backend

    @property
    def llm_registry(self) -> dict[str, LLMChatProtocol]:
        """Read-only view of the B-class client registry (a copy)."""
        return dict(self._llms)

    @property
    def tool_set(self) -> GovernedToolSet | None:
        """Nullable view of the wired A-class tool set.

        Additive public surface: unlike `tools` (which raises with
        guidance when unwired), this returns None so derived contexts
        (e.g. nested intervals) can mirror the parent's wiring exactly,
        unwired included.
        """
        return self._tools

    @property
    def has_memo_layer(self) -> bool:
        """Whether a memo engine is injected for this generation.

        Engine-aware components check this and fail with guidance
        (require_memo_layer) instead of an AttributeError frames away.
        """
        return self._memo is not None

    def require_memo_layer(self, feature: str) -> MemoLayerProtocol:
        """Return the memo layer, or raise with engine-tier guidance."""
        if self._memo is None:
            raise RuntimeError(
                f"{feature} requires an injected memo layer "
                f"(MemoLayerProtocol via GovernedAgent's "
                f"memo_layer_factory); the passthrough tier has none"
            )
        return self._memo

    async def memoize(
            self,
            purpose: str,
            seq: int | str,
            inputs: dict,
            call: Callable[[], Awaitable[dict]],
            *,
            reuse: str = "always",
            record_origin: bool = True,
            origin_seq: int | str | None = None,
            category: CallClass = CallClass.READONLY,
            mode: str | None = None,
    ) -> tuple[dict, str]:
        """Wrap one A-class call in the memo layer.

        Returns (result, origin) where origin is "executed",
        "reused:<origin_call_id>" or "stubbed:<purpose>".
        record_origin appends the origin to self.origins[purpose] for
        the implementation's lineage reporting. origin_seq is forwarded
        to the memo layer when the memo slot differs from the governed
        call's call_id slot.

        category declares the axis-B call class; reuse="never" is a
        legacy alias for category=producer and refuses every override.
        mode is the pre-resolved mode for callers that already ran the
        resolver against a tool side-effect tag (TrackedToolSet); when
        omitted the mode is resolved here from category + the tool's
        registered side-effect tag + the declared reuse, so business
        impls and tracked atoms route identically through the single
        resolution chain.
        """
        if reuse == "never":
            category = CallClass.PRODUCER
        if mode is None:
            mode = self._resolver.resolve(
                purpose, category,
                side_effect=self.tool_side_effect(purpose),
                declared=reuse,
            )
        if mode == STUB:
            # A stubbed call never executes, never touches the memo
            # cache, and records evidence on two tracks: the origins
            # list (archived with the generation result) and a direct
            # audit event (countable from the audit stream alone).
            origin = f"stubbed:{purpose}"
            if record_origin:
                self.record_origin(purpose, origin)
            await self.record_stub_audit(
                purpose, seq=seq, inputs=inputs, origin_seq=origin_seq,
            )
            return {
                "stubbed": True,
                "tool": purpose,
                "would_have_called": {"inputs": inputs},
            }, origin
        if self._memo is None:
            # Mechanism-direct: no memo layer injected -> always execute.
            result, origin = await call(), "executed"
        else:
            result, origin = await self._memo.get_or_execute(
                purpose, seq, inputs, call, reuse=reuse,
                origin_seq=origin_seq, mode=mode,
            )
        if record_origin:
            self.record_origin(purpose, origin)
        return result, origin

    # ---- evidence --------------------------------------------------------

    def record_origin(self, purpose: str, origin: str) -> None:
        """Append one memo origin under a purpose key (list shape)."""
        entry = self.origins.get(purpose)
        if entry is None:
            self.origins[purpose] = [origin]
        elif isinstance(entry, list):
            entry.append(origin)
        else:
            self.origins[purpose] = [entry, origin]

    async def record_stub_audit(
            self,
            purpose: str,
            *,
            seq: int | str | None,
            inputs: dict,
            origin_seq: int | str | None = None,
            tool_name: str | None = None,
    ) -> None:
        """Audit one stubbed call directly (bypasses the call plane).

        The audit event carries the call id the real call WOULD have
        used (origin_seq when the memo slot differs from the call's
        own slot), keeping the stub record resolvable against the same
        naming discipline as executed calls. Degrades silently when
        the tool registry has no direct-audit surface (legacy fakes,
        minimal stubs): the origins record remains the fallback
        evidence.
        """
        recorder = getattr(self._tools, "record_stub_decision", None)
        if recorder is None:
            return
        call_id = make_call_id(
            purpose, self._meta.task_id, self._meta.eid,
            seq=seq if origin_seq is None else origin_seq,
        )
        await recorder(
            tool_name or purpose,
            call_id=call_id,
            inputs=inputs,
            policy_table=self._resolver.table,
        )

    def origin_of(self, purpose: str) -> str | None:
        """Latest recorded origin for a purpose, or None."""
        entry = self.origins.get(purpose)
        if entry is None:
            return None
        if isinstance(entry, list):
            return entry[-1] if entry else None
        return entry

    async def archive(self, result: dict, pins: dict[str, str]) -> None:
        """Persist this generation's result + lineage pins."""
        from ordigovernance.runtime.archive.archive import archive_generation

        await archive_generation(
            self._archive_backend, task_id=self._meta.task_id,
            eid=self._meta.eid, result=result, pins=pins,
        )