"""ordigovernance-gateway: HTTP execution front of the governance hot path.

The gateway is the WRITE path (execution front) for remote
orchestrators (n8n); the viewer routers remain the READ path
(evidence). Redis primitives never leave this process: HTTP
terminates at governed-call / task-action granularity.
"""

from ordigovernance.gateway.app import build_app

__version__ = "0.1.0"

__all__ = ["build_app"]