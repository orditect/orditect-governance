"""ordigovernance-runtime: mechanism-direct governance runtime.

Public surface: governed agent/task assembly, governed tools, the
archive, lifecycle helpers, orchestration patterns, stream adapters
and the open replay data shapes. Engine intelligence (memo reuse,
policy routing, drift attribution) plugs in behind the api protocols
at assembly time.
"""

from ordigovernance.runtime.agent.context import AgentContext, PassthroughResolver
from ordigovernance.runtime.agent.governed_agent import (
    AgentProtocol,
    GovernedAgent,
    assemble_agent,
)
from ordigovernance.runtime.atoms import (
    PassthroughTrackedLLM,
    PassthroughTrackedToolSet,
)
from ordigovernance.runtime.archive.archive import (
    archive_generation,
    gen_result_key,
    load_generation,
    load_pinned_by,
    pinned_by_key,
)
from ordigovernance.runtime.replay.spec import (
    ReplayInput,
    ReplayRangeReport,
    ReplayReport,
    ReplaySpec,
)
from ordigovernance.runtime.replay.topo_sort import subgraph_between, topo_order
from ordigovernance.runtime.task.governed_task import (
    GenerationMeta,
    GovernedTask,
    TaskIO,
)
from ordigovernance.runtime.tools.governed_tools import GovernedToolSet
from ordigovernance.runtime.replay.driver import ReplayDriver
from ordigovernance.runtime.replay.spec import (
    ReplayInput,
    ReplayRangeReport,
    ReplayReport,
    ReplaySpec,
)
from ordigovernance.runtime.replay.topo_sort import subgraph_between, topo_order

__version__ = "0.1.0"

__all__ = [
    "AgentContext",
    "AgentProtocol",
    "GenerationMeta",
    "GovernedAgent",
    "GovernedTask",
    "GovernedToolSet",
    "PassthroughResolver",
    "PassthroughTrackedLLM",
    "PassthroughTrackedToolSet",
    "ReplayDriver",
    "ReplayInput",
    "ReplayRangeReport",
    "ReplayReport",
    "ReplaySpec",
    "TaskIO",
    "archive_generation",
    "assemble_agent",
    "gen_result_key",
    "load_generation",
    "load_pinned_by",
    "pinned_by_key",
    "subgraph_between",
    "topo_order",
]