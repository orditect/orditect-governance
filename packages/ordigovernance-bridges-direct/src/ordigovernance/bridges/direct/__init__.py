"""Direct bridge: run governed workflows without an orchestration framework.

Reusable assembly only — every business fact (handlers, prompts, task
builders, limits, endpoints) arrives as a parameter. Import the pieces:

    from ordigovernance.bridges.direct import run
"""

from ordigovernance.bridges.direct.context import build_hot_path
from ordigovernance.bridges.direct.llms import build_client_registry
from ordigovernance.bridges.direct.runner import run
from ordigovernance.bridges.direct.tools import build_tool_set

__all__ = [
    "build_client_registry",
    "build_hot_path",
    "build_tool_set",
    "run",
]