"""ordigovernance-api: public contracts of the ordigovernance governance stack.

Zero third-party dependencies. Everything here is a protocol, a pure
data shape, or policy vocabulary — the stable anchor the open runtime,
bridges and engine implementations all build against.
"""

from ordigovernance.api.atoms import TrackedLLMProtocol, TrackedToolSetProtocol
from ordigovernance.api.context import (
    DriftEngineProtocol,
    MemoLayerProtocol,
    PinFindingShape,
    PolicyResolverProtocol,
    ReconcileFnProtocol,
    ReconcileReportShape,
)
from ordigovernance.api.side_effect import (
    ALLOW,
    SANDBOX,
    STUB,
    EXTERNAL_MODES,
    REUSE_MODES,
    CallClass,
    ReusePolicy,
    SideEffect,
    normalize_side_effect,
)
from ordigovernance.api.task import GenerationMeta, TaskIO
from ordigovernance.api.tools import (
    RESERVED_PAYLOAD_KEYS,
    check_reserved_payload_keys,
)

__version__ = "0.1.0"

__all__ = [
    "ALLOW",
    "SANDBOX",
    "STUB",
    "EXTERNAL_MODES",
    "REUSE_MODES",
    "CallClass",
    "DriftEngineProtocol",
    "GenerationMeta",
    "MemoLayerProtocol",
    "PinFindingShape",
    "PolicyResolverProtocol",
    "RESERVED_PAYLOAD_KEYS",
    "ReconcileFnProtocol",
    "ReconcileReportShape",
    "ReusePolicy",
    "SideEffect",
    "TaskIO",
    "TrackedLLMProtocol",
    "TrackedToolSetProtocol",
    "check_reserved_payload_keys",
    "normalize_side_effect",
]