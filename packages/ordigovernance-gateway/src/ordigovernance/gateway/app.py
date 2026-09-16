"""build_app: gateway FastAPI assembly with lifespan wiring.

Startup order: registry loading and validation -> semaphore table
check -> hot path -> ambient run. Shutdown reverses it. The app is
constructed via `build_app()` (uvicorn --factory) so settings never
need module-level globals.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ordigovernance.gateway.config import GatewaySettings
from ordigovernance.gateway.registry import load_registry
from ordigovernance.gateway.routes.governed import build_governed_router
from ordigovernance.gateway.routes.hitl import build_hitl_router
from ordigovernance.gateway.routes.runs import build_runs_router
from ordigovernance.gateway.security import build_auth_dependency
from ordigovernance.gateway.session import SessionManager
from ordigovernance.gateway.routes.composites import build_composites_router

log = logging.getLogger(__name__)


def build_app(settings: GatewaySettings | None = None,
              *, env_path: Path | None = None,
              registry=None) -> FastAPI:
    """Assemble the gateway application.

    settings=None loads configuration from the environment (the
    deployment path); tests pass an explicit GatewaySettings.
    registry=None loads the deployment registry from entry points and
    settings.registry_module; tests inject one directly.
    """
    settings = settings or GatewaySettings.from_env(env_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loaded = (registry if registry is not None
                  else load_registry(settings.registry_module))
        manager = SessionManager(settings)
        await manager.startup(loaded)
        app.state.manager = manager
        app.state.registry = loaded
        log.info("gateway up: redis=%s trace_root=%s",
                 settings.redis_url or "in-memory", settings.trace_root)
        try:
            yield
        finally:
            await manager.shutdown()

    app = FastAPI(title="ordigovernance-gateway",
                  version="0.1.0", lifespan=lifespan)

    auth = build_auth_dependency(settings.auth_token)

    def _manager() -> SessionManager:
        return app.state.manager

    @app.get("/healthz")
    async def healthz() -> dict:
        manager = app.state.manager
        active = manager.active
        return {
            "status": "ok",
            "active_run": active.run_id if active else None,
            "ambient": manager.ambient is not None,
        }

    app.include_router(build_governed_router(_manager,
                                             auth_dependency=auth))
    app.include_router(build_runs_router(_manager,
                                         auth_dependency=auth))
    app.include_router(build_hitl_router(_manager,
                                         auth_dependency=auth))
    app.include_router(build_composites_router(_manager,
                                               auth_dependency=auth))
    return app
