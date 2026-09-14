"""Deterministic mock tool handlers: zero-infrastructure test fixtures.

Stand-ins for real tools (web search, deep retrieval, vector store,
memory body). Determinism is the point: snippet counts derive from
content hashes, so scripted narratives reproduce across runs and
generations — the acceptance ground (examples/) and any bridge's
contract suite can rely on identical outputs for identical inputs.

The memory body is process-local by default. Long-lived servers that
reload mid-session (uvicorn --reload) can persist it to a JSON file via
configure_memory_body() so memo/archive evidence survives a reload
between a run and its replay.

These handlers are plain async callables: exactly the shape a governed
wrapper wraps without modification.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _stable_int(text: str) -> int:
    """Stable hash for deterministic mock outputs (no randomness)."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


async def web_search(query: str, max_results: int = 4) -> dict:
    """Mock web search: synthetic snippets about the query.

    The snippet count derives from the query hash and always lands in
    [1, 5], so some inputs trigger a deepen-style second pass and
    others do not — deterministic per-input tool-path divergence.
    """
    count = 1 + _stable_int(query) % 5
    snippets = [
        {
            "title": f"Source {i + 1} on {query}",
            "url": (f"https://example.com/"
                    f"{hashlib.sha256(f'{query}-{i}'.encode()).hexdigest()[:8]}"),
            "snippet": (
                f"Snippet {i + 1} for '{query}': key facts, figures and "
                f"context a real search backend would return."
            ),
        }
        for i in range(count)
    ]
    return {"query": query, "count": count, "snippets": snippets[:max_results]}


async def deepen_search(query: str, focus: str) -> dict:
    """Mock deep retrieval: a second-pass tool call for rich results."""
    seed = _stable_int(f"{query}|{focus}")
    return {
        "query": query,
        "focus": focus,
        "documents": [
            {
                "doc_id": f"doc-{seed % 997}-{i}",
                "excerpt": (
                    f"Deep-dive excerpt {i + 1} on '{focus}' within "
                    f"'{query}': detailed evidence retrieved by the "
                    f"second-pass tool."
                ),
            }
            for i in range(2)
        ],
    }


async def vector_query(query: str, top_k: int = 3) -> dict:
    """Mock vector retrieval over the query's embedding neighborhood."""
    seed = _stable_int(query)
    return {
        "query": query,
        "hits": [
            {
                "doc_id": f"vec-{seed % 991}-{i}",
                "score": round(0.95 - i * 0.06, 3),
                "text": (
                    f"Embedding match {i + 1} for '{query}': background "
                    f"context a real vector store would return."
                ),
            }
            for i in range(top_k)
        ],
    }


# ---- mock memory body ------------------------------------------------------

_MEMORY: dict[str, dict] = {}
_PERSIST_PATH: Path | None = None


def configure_memory_body(persist_path: str | Path | None = None) -> None:
    """Point the mock memory body at a JSON file so it survives reloads.

    The file is loaded once at configure time and rewritten atomically
    on every write/reset. Without a path the body stays process-local
    (the default, used by tests). Calling this again with a different
    path rebinds and reloads; a corrupt file degrades to empty, never
    crashes the server.
    """
    global _PERSIST_PATH
    _PERSIST_PATH = Path(persist_path) if persist_path is not None else None
    if _PERSIST_PATH is None:
        return
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _PERSIST_PATH.is_file():
        try:
            data = json.loads(_PERSIST_PATH.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("mock memory file unreadable, starting empty: %s", e)
            data = {}
        if isinstance(data, dict):
            _MEMORY.clear()
            _MEMORY.update(data)


def _persist() -> None:
    """Best-effort atomic dump with read-merge-write semantics.

    Multiple processes share this file (the app and any CLI runs).
    Each process holds an in-memory snapshot loaded at configure
    time; a naive full dump would overwrite whatever other processes
    wrote since that snapshot was taken. The observed failure shape:
    the app boots BEFORE any CLI run writes the file, holds an empty
    snapshot forever, and a later replay/curate dump erases every
    CLI-run archive (baseline archives gone, frozen replays then
    mislabel "explained: mixed" instead of "identical").

    Merge the current file content with this process's writes
    instead: same-key entries use this process's version, every
    other process's keys are preserved. The in-memory snapshot is
    then refreshed to the merged state so later writes in this
    process keep carrying the merged keys. A persist failure never
    blocks a governed call.
    """
    if _PERSIST_PATH is None:
        return
    tmp = _PERSIST_PATH.with_name(_PERSIST_PATH.name + ".tmp")
    try:
        merged: dict = {}
        if _PERSIST_PATH.is_file():
            try:
                loaded = json.loads(_PERSIST_PATH.read_text())
                if isinstance(loaded, dict):
                    merged = loaded
            except (OSError, json.JSONDecodeError):
                # A torn or corrupt file degrades to this process's
                # own writes, never to a crash.
                merged = {}
        merged.update(_MEMORY)
        blob = json.dumps(merged, ensure_ascii=False)
        tmp.write_text(blob)
        tmp.replace(_PERSIST_PATH)
        _MEMORY.clear()
        _MEMORY.update(merged)
    except OSError as e:
        log.warning("mock memory persist failed (ignored): %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


async def memory_write(key: str, value: dict) -> dict:
    """Mock memory body: store a document by key."""
    _MEMORY[key] = value
    _persist()
    return {"key": key, "stored": True}


async def memory_read(key: str) -> dict:
    """Mock memory body: load a document by key (None when absent)."""
    return {"key": key, "value": _MEMORY.get(key)}


def memory_reset() -> None:
    """Clear the mock memory body (test isolation between cases)."""
    _MEMORY.clear()
    _persist()