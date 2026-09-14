"""Client registry assembly (D4: names are business-chosen)."""

from __future__ import annotations

from orditect.bridge.openai import GovernedLLMClient


def build_client_registry(
    base_url: str,
    *,
    api_key: str,
    clients: dict[str, dict],
    governor,
    budget,
    store,
    timeout: float = 120.0,
) -> dict:
    """Build {name: GovernedLLMClient} from {name: {"model": ..., "resource": ...}}."""
    return {
        name: GovernedLLMClient(
            base_url,
            api_key=api_key,
            governor=governor,
            resource=spec["resource"],
            budget=budget,
            audit_writer=store.audit,
            content_writer=store.content,
            model=spec["model"],
            timeout=timeout,
        )
        for name, spec in clients.items()
    }