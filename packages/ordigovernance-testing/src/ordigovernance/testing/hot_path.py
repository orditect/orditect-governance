"""In-memory hot path for zero-infrastructure runs and tests.

The orditect memory adapter implements the five cold-path store
domains only; the task hot records, the admission quota ledger, and
the semaphore registry have no in-memory framework implementation.
This module assembles the three minimal fakes the acceptance ground
and the quickstart need, matching the redis variants' contracts:

  - MemoryTaskStorage: task hot records with reopen semantics
    (previous_execution_ids chain, terminal statuses);
  - MemoryQuota: the BudgetLedger's duck-typed surface
    (reserve_units / get_pending_units), mirroring
    AdmissionQuotaRedisDB's response shapes;
  - MemoryLimiterRegistry + _MemorySemaphore: acquire/release with
    lease tokens, and the registry's status read surface.

These are TEST FIXTURES: production hot paths use the redis
adapters. The fakes exist so the acceptance narrative (governed
calls, generations, budget charging, water levels) executes exactly
as it does over redis, without a server.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

TERMINAL_WORDS = frozenset({"succeeded", "failed", "cancelled"})


class MemoryTaskStorage:
    """Task hot records: the full surface the framework touches.

    Mirrors the framework's storage contract:

      initialize_task(task_id, *, initial_status, parent_task_id,
                      if_not_exists, **fields) -> bool (created?)
      get_task / bulk_get_tasks / list_children /
      list_task_ids_by_status / update_task(validate_status_transfer)
      request_cancel / reopen_task

    Status values are plain strings ("pending", "queued", "running",
    "succeeded", "failed", "cancelled"); the flow layer's state
    machine owns transition validation.
    """

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    async def connect(self) -> None:
        return None

    # ---- reads ---------------------------------------------------------

    async def get_task(self, task_id: str) -> dict:
        return dict(self.records.get(task_id, {}))

    async def bulk_get_tasks(self, task_ids) -> list[dict]:
        return [dict(self.records.get(tid, {})) for tid in task_ids]

    async def list_children(self, task_id: str) -> list[str]:
        return [tid for tid, rec in self.records.items()
                if rec.get("parent_task_id") == task_id]

    async def list_task_ids_by_status(self, status: str) -> list[str]:
        return [tid for tid, rec in self.records.items()
                if rec.get("status") == status]

    # ---- writes ----------------------------------------------------------

    async def initialize_task(
            self,
            task_id: str,
            *,
            initial_status: str = "pending",
            parent_task_id: str | None = None,
            if_not_exists: bool = False,
            **fields,
    ) -> bool:
        """Create the hot record; returns True when actually created."""
        if if_not_exists and task_id in self.records:
            return False
        rec = self.records.setdefault(task_id, {})
        rec.setdefault("previous_execution_ids", [])
        rec["status"] = initial_status
        if parent_task_id is not None:
            rec["parent_task_id"] = parent_task_id
        rec.update(fields)
        rec.setdefault("execution_id", f"exec-{uuid.uuid4().hex[:12]}")
        return True

    async def create_task(self, task_id: str, **fields) -> None:
        await self.initialize_task(task_id, **fields)

    async def update_task(self, task_id: str, patch: dict | None = None,
                          validate_status_transfer: bool = True,
                          updates: dict | None = None,
                          **fields) -> None:
        """Apply a patch to the hot record.

        The framework uses two call shapes:
          update_task(task_id, {"status": ...})                    (positional)
          update_task(task_id, updates={...},                      (keyword)
                      validate_status_transfer=False)
        Both land here; validate_status_transfer is signature parity
        (the Lua-side state machine), ignored by the fake.
        """
        rec = self.records.setdefault(task_id, {})
        if patch:
            rec.update(patch)
        if updates:
            rec.update(updates)
        fields.pop("validate_status_transfer", None)
        if fields:
            rec.update(fields)

    async def request_cancel(self, task_id: str) -> bool:
        """Mark cancel_requested; True when the record exists."""
        if task_id not in self.records:
            return False
        self.records[task_id]["cancel_requested"] = True
        return True

    async def reopen_task(self, task_id: str) -> None:
        """Mint a new generation: the previous chain advances.

        Mirrors the production storage semantics: the old status is
        recorded as previous_status (the engine tier's memo layer
        reads it for on_resume routing), the stale result is cleared
        (a reopened record must not serve the previous generation's
        outputs while the new generation is still pending), and any
        cancel request is consumed by the reopen.
        """
        rec = self.records[task_id]
        prevs = list(rec.get("previous_execution_ids", []))
        current = rec.get("execution_id")
        if current:
            prevs.append(current)
        rec["previous_execution_ids"] = prevs
        rec["previous_status"] = rec.get("status")
        rec["execution_id"] = f"exec-{uuid.uuid4().hex[:12]}"
        rec["status"] = "pending"
        rec.pop("cancel_requested", None)
        rec.pop("result", None)

class MemoryQuota:
    """Admission quota ledger (BudgetLedger's duck-typed surface).

    Mirrors AdmissionQuotaRedisDB's contract:

      reserve_units(*, scope, task_id, units, max_units,
                    task_ttl_sec=None) -> {"ok": bool, "reason": str,
                                           "current": int,
                                           "reserved": int}
      get_pending_units(*, scope) -> int

    units=0 registers the lease slot without consuming quota (the
    ledger-creation call); units<0 is rejected. Idempotency: the same
    task_id reserving twice reports already_reserved without
    double-counting. max_units is enforced so budget-cap halts behave
    exactly as over redis.
    """

    def __init__(self) -> None:
        # scope -> {task_id: units}
        self._leases: dict[str, dict[str, int]] = {}

    async def connect(self) -> None:
        return None

    async def reserve_units(self, *, scope: str, task_id: str,
                            units: int, max_units: int,
                            task_ttl_sec: int | None = None,
                            **kwargs) -> dict:
        if units < 0:
            return {"ok": False, "reason": "invalid_units",
                    "current": await self.get_pending_units(scope=scope),
                    "reserved": 0}
        leases = self._leases.setdefault(scope, {})
        if task_id in leases:
            return {"ok": True, "reason": "already_reserved",
                    "current": await self.get_pending_units(scope=scope),
                    "reserved": leases[task_id]}
        current = await self.get_pending_units(scope=scope)
        if current + units > max_units:
            return {"ok": False, "reason": "limit_exceeded",
                    "current": current, "reserved": 0}
        leases[task_id] = units
        return {"ok": True, "reason": "",
                "current": current + units, "reserved": units}

    async def get_pending_units(self, *, scope: str) -> int:
        return sum(self._leases.get(scope, {}).values())


class _LeaseToken:
    """Opaque lease token with the .value attribute the adapter keys on."""

    def __init__(self, resource: str) -> None:
        self.value = uuid.uuid4().hex
        self.resource = resource

    def __repr__(self) -> str:  # pragma: no cover
        return f"LeaseToken({self.resource}:{self.value[:8]})"



class _MemorySemaphore:
    """One named semaphore with lease-token acquire/release.

    The TaskbaseGovernorAdapter keys its internal token map on
    token.value and may reconstruct a LeaseToken on release; the fake
    returns a token object carrying .value and accepts any token
    shape back on release.
    """

    def __init__(self, name: str, limit: int) -> None:
        self.name = name
        self.limit = limit
        self.in_use = 0

    async def acquire(self, *, timeout: float | None = None,
                      **kwargs) -> _LeaseToken:
        deadline = (asyncio.get_running_loop().time() + timeout
                    if timeout else None)
        while self.in_use >= self.limit:
            if deadline is not None \
                    and asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(
                    f"semaphore {self.name!r} acquire timed out")
            await asyncio.sleep(0.01)
        self.in_use += 1
        return _LeaseToken(self.name)

    async def try_acquire(self, **kwargs) -> _LeaseToken | None:
        if self.in_use >= self.limit:
            return None
        self.in_use += 1
        return _LeaseToken(self.name)

    async def release(self, token: Any = None, **kwargs) -> None:
        self.in_use = max(0, self.in_use - 1)

    def status(self) -> dict:
        usage = self.in_use
        util = f"{usage / self.limit:.1%}" if self.limit else "?"
        return {"name": self.name, "limit": self.limit, "usage": usage,
                "available": self.limit - usage, "utilization": util}


class MemoryLimiterRegistry:
    """Registry with the same read surface as the framework registry."""

    def __init__(self) -> None:
        self._sems: dict[str, _MemorySemaphore] = {}

    def register_semaphore(self, name: str, *args, limit: int = 1,
                           **kwargs) -> _MemorySemaphore:
        sem = _MemorySemaphore(name, limit)
        self._sems[name] = sem
        return sem

    def get_semaphore(self, name: str) -> _MemorySemaphore | None:
        return self._sems.get(name)

    def has_semaphore(self, name: str) -> bool:
        return name in self._sems

    def get_semaphore_usage(self, name: str) -> int:
        sem = self._sems.get(name)
        return sem.in_use if sem else 0

    def get_semaphore_status(self, name: str) -> dict:
        sem = self._sems.get(name)
        if sem is None:
            return {"name": name, "limit": 0, "usage": 0,
                    "available": 0, "utilization": "?"}
        return sem.status()

    async def get_all_semaphore_status(self) -> list[dict]:
        return [sem.status() for sem in self._sems.values()]

    def clear(self) -> None:
        self._sems.clear()


def build_memory_hot_path(
        semaphores: dict[str, int]) -> dict:
    """Assemble {storage, governor, quota, registry} fully in memory.

    The governor is the framework's TaskbaseGovernorAdapter over the
    memory registry, exactly as the direct bridge wraps the redis
    registry.
    """
    from orditect.flow.governor.factory import TaskbaseGovernorAdapter

    storage = MemoryTaskStorage()
    quota = MemoryQuota()
    registry = MemoryLimiterRegistry()
    for name, limit in semaphores.items():
        registry.register_semaphore(name, limit=limit)
    return {
        "storage": storage,
        "governor": TaskbaseGovernorAdapter(registry),
        "quota": quota,
        "registry": registry,
    }