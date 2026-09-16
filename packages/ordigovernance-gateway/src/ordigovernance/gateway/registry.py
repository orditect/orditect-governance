"""Deployment-injected factories: the gateway ships protocols only.

Loading channels (D6):

  1. entry points groups: ordigovernance.gateway.tools / .impls /
     .composites; each entry point loads to a callable returning a
     contribution dict {"tools": {...}, "impls": {...},
     "composites": {...}} (any subset).
  2. settings.registry_module as "module:callable"; the callable
     returns a GatewayRegistry or a contribution dict and OVERRIDES
     entry-point contributions on name conflicts (with a warning).

Dynamic loading bypasses the static import-boundary gate, so every
loaded factory is re-checked against the forbidden closed-tier
namespaces at boot: a hit refuses the boot and lists the offender.

Factory contracts (documented, structural):
  ToolHandlerFactory: (session) -> ToolHandler (async callable)
  ImplFactory:        (params: dict, surfaces) -> AgentProtocol
  CompositeFactory:   (params: dict, session) -> async driver
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ordigovernance.api.side_effect import normalize_side_effect

log = logging.getLogger(__name__)

FORBIDDEN_NAMESPACES = ("orditect_components", "ordienterprise")

ENTRY_POINT_GROUPS = {
    "tools": "ordigovernance.gateway.tools",
    "impls": "ordigovernance.gateway.impls",
    "composites": "ordigovernance.gateway.composites",
}


class RegistryError(RuntimeError):
    """Registry loading or validation failed; the gateway must not boot."""


@dataclass(frozen=True)
class ToolSpec:
    factory: Callable
    resource: str
    event_type: str
    side_effect: str = "readonly"
    description: str = ""


@dataclass(frozen=True)
class ImplSpec:
    factory: Callable
    description: str = ""


@dataclass(frozen=True)
class CompositeSpec:
    factory: Callable
    description: str = ""


@dataclass
class GatewayRegistry:
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    impls: dict[str, ImplSpec] = field(default_factory=dict)
    composites: dict[str, CompositeSpec] = field(default_factory=dict)


_SPEC_TYPES = {"tools": ToolSpec, "impls": ImplSpec,
               "composites": CompositeSpec}


def _normalize_spec(table: str, name: str, value: Any) -> Any:
    spec_type = _SPEC_TYPES[table]
    if isinstance(value, spec_type):
        return value
    if isinstance(value, dict):
        try:
            return spec_type(**value)
        except TypeError as e:
            raise RegistryError(
                f"{table}.{name}: malformed spec {value!r}: {e}") from None
    raise RegistryError(
        f"{table}.{name}: expected a {spec_type.__name__} or dict, "
        f"got {type(value).__name__}")


def _as_contribution(obj: Any, *, source: str) -> dict:
    if isinstance(obj, GatewayRegistry):
        return {"tools": obj.tools, "impls": obj.impls,
                "composites": obj.composites}
    if isinstance(obj, dict):
        return obj
    raise RegistryError(
        f"registry contribution from {source} must be a GatewayRegistry "
        f"or a dict, got {type(obj).__name__}")


def _merge(registry: GatewayRegistry, contribution: dict, *,
           source: str) -> None:
    for table in ("tools", "impls", "composites"):
        target = getattr(registry, table)
        for name, value in (contribution.get(table) or {}).items():
            if name in target:
                log.warning("registry: %s overrides %s %r", source,
                            table, name)
            target[name] = _normalize_spec(table, name, value)


def _load_entry_points(registry: GatewayRegistry) -> None:
    from importlib.metadata import entry_points

    for group in ENTRY_POINT_GROUPS.values():
        for ep in entry_points(group=group):
            try:
                provider = ep.load()
                contribution = _as_contribution(provider(), source=ep.name)
            except RegistryError:
                raise
            except Exception as e:
                raise RegistryError(
                    f"entry point {ep.name!r} (group {group}) failed "
                    f"to load: {e}") from None
            _merge(registry, contribution, source=f"entry point {ep.name}")


def _load_module(registry: GatewayRegistry, module_path: str) -> None:
    module_name, sep, attr = module_path.partition(":")
    if not sep:
        raise RegistryError(
            f"registry_module must look like 'module:callable', "
            f"got {module_path!r}")
    try:
        provider = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as e:
        raise RegistryError(
            f"registry_module {module_path!r} failed to load: {e}") from None
    contribution = _as_contribution(provider(), source=module_path)
    _merge(registry, contribution, source=module_path)


def validate_registry(registry: GatewayRegistry) -> None:
    """Spec integrity plus the forbidden-namespace self-check (D6)."""
    offenders: list[str] = []
    for table in ("tools", "impls", "composites"):
        for name, spec in getattr(registry, table).items():
            factory = spec.factory
            if not callable(factory):
                raise RegistryError(
                    f"{table}.{name}: factory is not callable")
            module = getattr(factory, "__module__", "") or ""
            for ns in FORBIDDEN_NAMESPACES:
                if module == ns or module.startswith(ns + "."):
                    offenders.append(f"{table}.{name} ({module})")
    if offenders:
        raise RegistryError(
            "registry entries from forbidden closed-tier namespaces "
            "(dynamic loading bypasses the static import gate): "
            + ", ".join(offenders))
    for name, spec in registry.tools.items():
        if not spec.resource or not spec.event_type:
            raise RegistryError(
                f"tools.{name}: resource and event_type are required")
        try:
            normalize_side_effect(spec.side_effect)
        except ValueError as e:
            raise RegistryError(f"tools.{name}: {e}") from None


def load_registry(module_path: str | None) -> GatewayRegistry:
    registry = GatewayRegistry()
    _load_entry_points(registry)
    if module_path:
        _load_module(registry, module_path)
    validate_registry(registry)
    log.info("registry loaded: %d tool(s), %d impl(s), %d composite(s)",
             len(registry.tools), len(registry.impls),
             len(registry.composites))
    return registry