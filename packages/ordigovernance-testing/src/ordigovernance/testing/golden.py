"""Golden trace bundle normalization and diffing.

A trace bundle (snapshots.ndjson / audit.ndjson / deps.ndjson) is full
of non-deterministic values: execution ids (uuid4), wall-clock
timestamps, elapsed milliseconds, token usage figures. Normalization
replaces them with stable placeholders so two runs of the same scripted
narrative diff empty when behavior is identical.

Mapping discipline: an execution id maps to "{task_id}#{ordinal}" where
the ordinal is the generation order of that id inside its task (first
appearance in the snapshots file, which is append-only and therefore
time-ordered). Unknown uuid-shaped ids degrade to a fixed placeholder
instead of breaking the diff.

Volatility discipline: behavioral equivalence is asserted by the EVENT
sequence (ids, types, order), never by measured numbers. Token usage,
cost units and elapsed times depend on the LLM endpoint and the wall
clock; they are dropped, not compared.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

BUNDLE_FILES = ("snapshots.ndjson", "audit.ndjson", "deps.ndjson")

# Keys whose values are measurements, not behavior: dropped on sight.
VOLATILE_KEYS = frozenset({
    "started_at",
    "finished_at",
    "timestamp",
    "created_at",
    "updated_at",
    "registered_at",
    "elapsed_ms",
    "duration_ms",
    "usage",
    "cost_units",
    "ts",  # audit event wall-clock timestamp
})

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _read_ndjson(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _collect_eids(snaps: list[dict]) -> dict[str, str]:
    """Map every execution id to a stable generation placeholder.

    previous_execution_ids are registered before the current id so the
    ordinal matches the true generation order even when an earlier
    generation's own row is missing from the file.
    """
    order: dict[str, list[str]] = {}
    for row in snaps:
        data = row.get("data", row)
        if not isinstance(data, dict):
            continue
        task_id = data.get("task_id")
        eid = data.get("execution_id")
        if not task_id or not eid:
            continue
        known = order.setdefault(task_id, [])
        for prev in data.get("previous_execution_ids", []):
            if prev not in known:
                known.append(prev)
        if eid not in known:
            known.append(eid)
    return {
        eid: f"{task_id}#{index + 1}"
        for task_id, eids in order.items()
        for index, eid in enumerate(eids)
    }


def _replace_ids(text: str, eid_map: dict[str, str]) -> str:
    for raw in sorted(eid_map, key=len, reverse=True):
        text = text.replace(raw, eid_map[raw])
    return _UUID_RE.sub("uuid#?", text)


_CONTENT_KEY_RE = re.compile(r"sha256/[0-9a-f]{2}/[0-9a-f]{64}")


def _normalize(value: Any, eid_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in VOLATILE_KEYS:
                continue
            # Result content hashes are content fingerprints: identical
            # call structure with different LLM text yields different
            # hashes. Normalized like content pointers so structural
            # diffs stay readable; content equivalence is asserted
            # elsewhere.
            if k == "result_hash" and isinstance(v, str):
                out[str(k)] = "hash#content"
            else:
                out[str(k)] = _normalize(v, eid_map)
        return out
    if isinstance(value, list):
        return [_normalize(v, eid_map) for v in value]
    if isinstance(value, str):
        text = _replace_ids(value, eid_map)
        # Content-addressed pointers hash the payload: identical call
        # structure with different LLM text yields different keys.
        # Normalized to a fixed placeholder so structural diffs stay
        # readable; content equivalence is asserted elsewhere.
        return _CONTENT_KEY_RE.sub("sha256/#content", text)
    return value



def normalize_bundle(trace_dir: str | Path) -> dict[str, list]:
    """Normalize one run's trace bundle into a diff-able structure."""
    trace_dir = Path(trace_dir)
    bundle = {name: _read_ndjson(trace_dir / name) for name in BUNDLE_FILES}
    eid_map = _collect_eids(bundle["snapshots.ndjson"])
    return {
        name: _normalize(rows, eid_map)
        for name, rows in bundle.items()
    }


def save_golden(bundle: dict[str, list], path: str | Path) -> None:
    """Persist a normalized bundle as the regression baseline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def load_golden(path: str | Path) -> dict[str, list]:
    return json.loads(Path(path).read_text())


def diff_bundles(
    golden: Any,
    current: Any,
    ignore_patterns: Iterable[str] = (),
) -> list[str]:
    """Path-level diff between two normalized bundles.

    ignore_patterns: regexes matched against the diff path — the
    whitelist for intentional changes (e.g. an origins shape
    migration). An empty result means behavioral equivalence.
    """
    ignores = [re.compile(p) for p in ignore_patterns]
    diffs: list[str] = []

    def _excluded(path: str) -> bool:
        return any(rx.search(path) for rx in ignores)

    def _walk(g: Any, c: Any, path: str) -> None:
        if _excluded(path):
            return
        if type(g) is not type(c):
            diffs.append(
                f"{path}: type {type(g).__name__} != {type(c).__name__}"
            )
            return
        if isinstance(g, dict):
            for key in sorted(set(g) | set(c)):
                child = f"{path}.{key}"
                if key not in g:
                    if not _excluded(child):
                        diffs.append(f"{child}: only in current")
                elif key not in c:
                    if not _excluded(child):
                        diffs.append(f"{child}: only in golden")
                else:
                    _walk(g[key], c[key], child)
        elif isinstance(g, list):
            if len(g) != len(c):
                diffs.append(f"{path}: length {len(g)} != {len(c)}")
            for index, (gv, cv) in enumerate(zip(g, c)):
                _walk(gv, cv, f"{path}[{index}]")
        elif g != c:
            diffs.append(f"{path}: {g!r} != {c!r}")

    _walk(golden, current, "$")
    return diffs

# Patterns covering BUSINESS variance in the reference narrative: LLM
# outputs (subtopics -> researcher ids, draft/analysis text) legitimately
# differ across runs. Governance STRUCTURE (event types, call_id shapes,
# generation counts, dependency arity) must still match.
BUSINESS_VARIANCE_PATTERNS = (
    r"task_id",           # researcher slugs embed LLM-chosen subtopics
    r"event_id",          # call ids embed task ids
    r"result",            # business payloads (draft/analysis/report text)
    r"subtopics", r"researcher_ids", r"researchers",
    r"child_id", r"parent_id",  # dynamic fan-out edges
    r"length",            # memo-hit counts follow the deepen divergence
)


def diff_structure(golden: Any, current: Any) -> list[str]:
    """Diff with business variance whitelisted: governance shape only."""
    return diff_bundles(golden, current,
                        ignore_patterns=BUSINESS_VARIANCE_PATTERNS)

def summarize(bundle: dict[str, list]) -> dict:
    """Order-insensitive governance summary of a normalized bundle.

    The audit log is an append-only CONCURRENT event stream: physical
    row order is scheduler timing, not behavior. Structural acceptance
    therefore compares aggregates, never positions. Memo traffic volume
    follows the business deepen divergence, so counts are reported as
    exact values and compared with explicit tolerances by the caller.
    """
    audit = bundle.get("audit.ndjson", [])
    snaps = bundle.get("snapshots.ndjson", [])
    deps = bundle.get("deps.ndjson", [])

    def _event_id(row: dict) -> str:
        data = row.get("data", row)
        return str(data.get("event_id", ""))

    def _purpose(event_id: str) -> str:
        # call_id shape: {purpose}-{task_id}-{execution_id}[-{seq}]
        return event_id.split("-", 1)[0] if event_id else ""

    type_counts: dict[str, int] = {}
    purpose_counts: dict[str, int] = {}
    for row in audit:
        data = row.get("data", row)
        etype = data.get("event_type", "?")
        type_counts[etype] = type_counts.get(etype, 0) + 1
        p = _purpose(_event_id(row))
        if p:
            purpose_counts[p] = purpose_counts.get(p, 0) + 1

    generations: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for row in snaps:
        data = row.get("data", row)
        task_id = data.get("task_id", "?")
        generations[task_id] = generations.get(task_id, 0) + 1
        status = data.get("status", "?")
        statuses[status] = statuses.get(status, 0) + 1

    return {
        "audit_total": len(audit),
        "event_types": type_counts,
        "call_purposes": purpose_counts,
        "node_count": len({r.get("data", r).get("task_id")
                           for r in snaps}),
        "snapshot_rows": len(snaps),
        "generations_per_node": generations,
        "terminal_statuses": statuses,
        "dep_edges": len(deps),
    }


def diff_summary(golden_sum: dict, current_sum: dict, *,
                 tolerances: dict[str, int] | None = None,
                 multiset_keys: frozenset = frozenset(
                     {"generations_per_node"})) -> list[str]:
    """Compare two summaries with per-key tolerance bands.

    tolerances: {"audit_total": 10, ...} — keys absent from the map
    must match exactly. Memo/divergence-driven counts belong in the
    tolerance band; governance invariants do not.

    multiset_keys: node-level maps whose KEYS carry business variance
    (researcher slugs embed LLM-chosen subtopics). Behavior lives in
    the values, so those maps compare as value multisets. Vocabulary
    maps (event types, call purposes) keep per-key comparison.

    Explainable variance: a deepen divergence has a fixed audit
    footprint (one tool call plus its memo traffic). When the ONLY
    call-purpose difference is deepen and the event-type deltas match
    its footprint, the variance is accounted for and reported as
    explained instead of a row-level diff.
    """
    tolerances = tolerances or {}
    diffs: list[str] = []
    for key in sorted(set(golden_sum) | set(current_sum)):
        g = golden_sum.get(key)
        c = current_sum.get(key)
        if isinstance(g, dict) and isinstance(c, dict):
            if key in multiset_keys:
                g_vals = sorted(g.values(), key=repr)
                c_vals = sorted(c.values(), key=repr)
                if g_vals != c_vals:
                    diffs.append(f"{key}: value multiset differs "
                                 f"({g_vals} != {c_vals})")
                continue
            for sub in sorted(set(g) | set(c)):
                gv, cv = g.get(sub), c.get(sub)
                if gv != cv:
                    diffs.append(f"{key}.{sub}: {gv!r} != {cv!r}")
            continue
        if isinstance(g, int) and isinstance(c, int):
            band = tolerances.get(key, 0)
            if abs(g - c) > band:
                diffs.append(f"{key}: {g} != {c} (tolerance {band})")
        elif g != c:
            diffs.append(f"{key}: {g!r} != {c!r}")

    explained = _explain_deepen_variance(golden_sum, current_sum)
    if explained:
        diffs = [d for d in diffs if not any(
            d.startswith(f"{prefix}.")
            for prefix in ("call_purposes", "event_types")
        )]
        diffs.append(f"explained: {explained}")
    return diffs


_DEEPEN_FOOTPRINT = {
    ("call_purposes", "memget"): 1,
    ("call_purposes", "memput"): 1,
    ("event_types", "memory_call"): 2,
    ("event_types", "tool_call"): 1,
}

def _explain_deepen_variance(golden_sum: dict,
                             current_sum: dict) -> str | None:
    """Attribute a deepen-only divergence to its audit footprint.

    The footprint spans BOTH sections: call_purposes (deepen itself,
    plus its memget/memput traffic) and event_types (tool_call plus
    memory_call). The divergence is explained iff the FULL set of
    differing entries across both sections equals the footprint, each
    scaled by the deepen delta.
    """
    g_purposes = golden_sum.get("call_purposes", {})
    c_purposes = current_sum.get("call_purposes", {})
    g_types = golden_sum.get("event_types", {})
    c_types = current_sum.get("event_types", {})

    deepen_delta = (c_purposes.get("deepen", 0)
                    - g_purposes.get("deepen", 0))
    if deepen_delta <= 0:
        return None

    # Collect every differing entry across both sections.
    deltas: dict[tuple[str, str], int] = {}
    for section, g_sec, c_sec in (
            ("call_purposes", g_purposes, c_purposes),
            ("event_types", g_types, c_types)):
        for name in set(g_sec) | set(c_sec):
            delta = c_sec.get(name, 0) - g_sec.get(name, 0)
            if delta:
                deltas[(section, name)] = delta

    # The footprint scaled by the deepen delta, plus deepen itself.
    expected = {key: unit * deepen_delta
                for key, unit in _DEEPEN_FOOTPRINT.items()}
    expected[("call_purposes", "deepen")] = deepen_delta

    if deltas != expected:
        return None
    return f"deepen divergence x{deepen_delta} (footprint verified)"