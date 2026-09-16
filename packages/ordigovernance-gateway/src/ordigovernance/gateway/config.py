"""Gateway configuration: env-driven settings with .env support.

Every expected configuration source reports what it did on startup
(path, parser, key count): an optional loader must never skip
silently (docs/pitfalls.md 15.1). Existing process environment
always wins over .env values.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

DEFAULT_SEMAPHORES: dict[str, int] = {
    "task_execution": 8,
    "llm": 2,
    "web_search": 2,
    "memo_store": 2,
}


def _count_env_keys(path: Path) -> int:
    """Count definition lines in one .env file (diagnostics only)."""
    count = 0
    try:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                count += 1
    except OSError:
        pass
    return count


def load_env_file(env_path: Path | None = None) -> tuple[str, int]:
    """Load one .env file; python-dotenv when installed, else built-in.

    The built-in parser accepts KEY=value lines, an optional `export`
    prefix and optional quotes, and is BOM/CRLF tolerant. Existing
    process environment always wins, matching python-dotenv's
    no-override default. Returns (source, keys) for the startup
    diagnostic line.
    """
    path = env_path or Path.cwd() / ".env"
    if not path.is_file():
        return f"no .env found at {path}", 0
    try:
        from dotenv import load_dotenv

        load_dotenv(path)
        return f"{path} (python-dotenv)", _count_env_keys(path)
    except ImportError:
        pass
    loaded = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
            loaded += 1
    return f"{path} (built-in parser)", loaded


def _json_object(raw: str, *, name: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{name} must be a JSON object: {e}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


@dataclass
class GatewaySettings:
    """Everything the gateway needs.

    Construct directly in tests; construct via from_env() in
    deployment. make_clients is the code-level injection seam (D12):
    tests inject scripted clients, None runs the default real
    assembly (a GovernedLLMClient registry over settings.model_clients).
    """

    redis_url: str | None = None
    semaphores: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SEMAPHORES))
    trace_root: Path = Path("data/gateway-runs")
    base_url: str | None = None
    api_key: str | None = None
    model_clients: dict[str, dict] = field(default_factory=dict)
    auth_token: str = ""
    default_budget_max_units: int = 100000
    ambient_budget_max_units: int = 10000000
    step_timeout: float = 300.0
    receipt_timeout: float = 30.0
    poll_interval: float = 0.2
    lease_time: float = 300.0
    registry_module: str | None = None
    make_clients: Callable[[dict], Awaitable[dict]] | None = None

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "GatewaySettings":
        source, loaded = load_env_file(env_path)
        log.info("env: %s (%d key(s))", source, loaded)
        env = os.environ

        semaphores = dict(DEFAULT_SEMAPHORES)
        raw = env.get("GATEWAY_SEMAPHORES")
        if raw:
            semaphores = {
                str(k): int(v)
                for k, v in _json_object(raw, name="GATEWAY_SEMAPHORES").items()
            }

        model_clients: dict[str, dict] = {}
        raw = env.get("GATEWAY_MODEL_CLIENTS")
        if raw:
            model_clients = {
                str(k): dict(v)
                for k, v in _json_object(raw, name="GATEWAY_MODEL_CLIENTS").items()
            }
        else:
            # Sensible default from the shared .env vocabulary: one
            # "research" client on the probe model, using the built-in
            # semaphore table's "llm" resource. An explicit
            # GATEWAY_MODEL_CLIENTS / GATEWAY_SEMAPHORES pair always
            # wins (they are one namespace, docs/pitfalls.md 2).
            probe_model = env.get("PROBE_MODEL") or "gpt-4o-mini"
            model_clients = {
                "research": {"model": probe_model, "resource": "llm"},
            }

        auth_token = env.get("GATEWAY_AUTH_TOKEN", "")
        if not auth_token:
            raise ValueError(
                "GATEWAY_AUTH_TOKEN is required: set it in the process "
                "environment or the .env file"
            )

        return cls(
            redis_url=env.get("GATEWAY_REDIS_URL") or env.get("REDIS_URL"),
            semaphores=semaphores,
            trace_root=Path(env.get("GATEWAY_TRACE_ROOT",
                                    "data/gateway-runs")),
            base_url=(env.get("OPENAI_BASE_URL")
                      or env.get("OPENAI_API_BASE")),
            api_key=env.get("OPENAI_API_KEY"),
            model_clients=model_clients,
            auth_token=auth_token,
            default_budget_max_units=int(
                env.get("GATEWAY_BUDGET_MAX_UNITS", "100000")),
            ambient_budget_max_units=int(
                env.get("GATEWAY_AMBIENT_BUDGET_MAX_UNITS", "10000000")),
            step_timeout=float(env.get("GATEWAY_STEP_TIMEOUT", "300")),
            receipt_timeout=float(env.get("GATEWAY_RECEIPT_TIMEOUT", "30")),
            poll_interval=float(env.get("GATEWAY_POLL_INTERVAL", "0.2")),
            lease_time=float(env.get("GATEWAY_LEASE_TIME", "300")),
            registry_module=env.get("GATEWAY_REGISTRY_MODULE") or None,
        )