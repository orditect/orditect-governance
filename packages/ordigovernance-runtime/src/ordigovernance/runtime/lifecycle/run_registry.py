"""Runs registry: every run's index entry plus its trace directory.

Migrated from the reference application's runtime registry. Each run
gets its own trace directory (runs/{run_id}/trace/) and one entry in
index.json. Historical runs are read purely from these cold-path
artifacts; a corrupt index degrades to empty, never crashes.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


def new_run_id() -> str:
    """Human-sortable run id: run-YYYYMMDD-HHMMSS-<short uuid>."""
    return time.strftime("run-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


class RunsRegistry:
    """File-backed run index bound to one base directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self._runs_dir = Path(base_dir)
        self._index_file = self._runs_dir / "index.json"

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def run_trace_dir(self, run_id: str) -> Path:
        return self._runs_dir / run_id / "trace"

    def _load_index(self) -> list[dict[str, Any]]:
        if not self._index_file.is_file():
            return []
        try:
            return json.loads(self._index_file.read_text())
        except Exception:
            return []  # corrupt index degrades to empty, never crashes

    def _save_index(self, entries: list[dict[str, Any]]) -> None:
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._index_file.parent / (self._index_file.name + ".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2))
        tmp.replace(self._index_file)

    def register_run(self, run_id: str, intent: str,
                     params: dict[str, Any], budget_scope: str) -> None:
        entries = self._load_index()
        entries.append({
            "run_id": run_id,
            "intent": intent,
            "params": params,
            "budget_scope": budget_scope,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "final_status": None,
            "budget_balance": None,
        })
        self._save_index(entries)

    def update_budget_scope(self, run_id: str, budget_scope: str) -> None:
        """Backfill the effective budget/memo scope for one run.

        The executor derives the run-scoped marker (see
        docs/pitfalls.md 12.1); the registry entry is the replay
        channel's only source for the ORIGINAL run's memo scope, so it
        must be updated once the executor knows it.

        Write-once: only an entry still holding its placeholder (the
        run_id itself) is backfilled. The registry is shared mutable
        state across interleaved runs (app + CLI on one hot path); an
        unconditional overwrite lets a LATER run's derivation land on
        an EARLIER run's entry, and every replay against the earlier
        run then derives a memo scope it never used (all keys miss).
        Prefer per-run evidence (a run_meta.json inside the trace
        bundle) when wiring new replay channels.
        """
        entries = self._load_index()
        for e in entries:
            if (e.get("run_id") == run_id
                    and e.get("budget_scope") == run_id):
                e["budget_scope"] = budget_scope
                break
        self._save_index(entries)

    def finish_run(self, run_id: str, *, final_status: str,
                   budget_balance: int | None) -> None:
        entries = self._load_index()
        for e in entries:
            if e.get("run_id") == run_id:
                e["status"] = "finished"
                e["finished_at"] = time.time()
                e["final_status"] = final_status
                e["budget_balance"] = budget_balance
                break
        self._save_index(entries)

    def list_runs(self) -> list[dict[str, Any]]:
        """Newest first, for the version selector."""
        return sorted(
            self._load_index(), key=lambda e: e.get("started_at", 0),
            reverse=True,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        for e in self._load_index():
            if e.get("run_id") == run_id:
                return e
        return None