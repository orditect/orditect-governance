"""Testing utilities: golden trace normalization, conformance kit and
deterministic fixtures for the ordigovernance stack."""

from ordigovernance.testing.conformance import (
    check_archive_document,
    check_call_id_shape,
    check_memo_envelope,
    check_pinned_by_document,
    check_seq_bands,
    run_engine_memo_profile,
    run_producer_profile,
)
from ordigovernance.testing.golden import (
    BUNDLE_FILES,
    BUSINESS_VARIANCE_PATTERNS,
    VOLATILE_KEYS,
    diff_bundles,
    diff_structure,
    diff_summary,
    load_golden,
    normalize_bundle,
    save_golden,
    summarize,
)
from ordigovernance.testing.hot_path import (
    MemoryLimiterRegistry,
    MemoryQuota,
    MemoryTaskStorage,
    build_memory_hot_path,
)
from ordigovernance.testing.mock_backend import HandlerBackendAdapter
from ordigovernance.testing.mock_llm import ScriptedLLMClient
from ordigovernance.testing.mock_tools import (
    configure_memory_body,
    deepen_search,
    memory_read,
    memory_reset,
    memory_write,
    vector_query,
    web_search,
)
# Re-export the mock_tools submodule so that
# `from ordigovernance.testing import mock_tools` works as a namespace import.
from ordigovernance.testing import mock_tools  # noqa: F401

__all__ = [
    "BUNDLE_FILES",
    "BUSINESS_VARIANCE_PATTERNS",
    "HandlerBackendAdapter",
    "MemoryLimiterRegistry",
    "MemoryQuota",
    "MemoryTaskStorage",
    "ScriptedLLMClient",
    "VOLATILE_KEYS",
    "build_memory_hot_path",
    "check_archive_document",
    "check_call_id_shape",
    "check_memo_envelope",
    "check_pinned_by_document",
    "check_seq_bands",
    "configure_memory_body",
    "deepen_search",
    "diff_bundles",
    "diff_structure",
    "diff_summary",
    "load_golden",
    "memory_read",
    "memory_reset",
    "memory_write",
    "mock_tools",
    "normalize_bundle",
    "run_engine_memo_profile",
    "run_producer_profile",
    "save_golden",
    "summarize",
    "vector_query",
    "web_search",
]