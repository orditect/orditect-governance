"""Probe: real-redis reopen semantics vs the in-memory fixture.

Locates the generation-chain divergence observed in the real-redis
acceptance run: a manually reopened node (storage.reopen_task +
orchestrator.submit) lost its previous_execution_ids chain
(prevs=0), while a sink-driven resume on the same hot path kept it
(prevs=1). Two hypothesis branches:

  A. TaskRedisDB.reopen_task deletes and recreates the record
     (chain lost at reopen)           -> align the fixture to reality.
  B. reopen_task is correct, but the orchestrator's submit-side
     initialize_task resets the record -> fix the app/orditect path.

Requires a local redis (same instance the acceptance run used).
Exit code 0 always: this script prints facts; the human decides.
"""

from __future__ import annotations

import asyncio
import os
import sys

import redis.asyncio as aioredis


def _fmt(record: dict) -> str:
    if not record:
        return "<missing>"
    return (f"status={record.get('status')} "
            f"eid={record.get('execution_id')} "
            f"prevs={record.get('previous_execution_ids')} "
            f"previous_status={record.get('previous_status')} "
            f"result={'yes' if record.get('result') else 'no'}")


async def main(redis_url: str) -> None:
    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.ping()

    from orditect.flow.storage.factory import get_default_storage

    storage = get_default_storage(client)
    await storage.connect()

    task_id = "probe-reopen-semantics"
    await client.delete(f"task:{task_id}")

    print(f"redis: {redis_url}")
    print(f"storage: {type(storage).__name__}")
    print()

    await storage.initialize_task(task_id, initial_status="succeeded")
    await storage.update_task(task_id, {"result": {"gen": 1}})
    r1 = await storage.get_task(task_id)
    print(f"1. initialized+succeeded : {_fmt(r1)}")

    await storage.reopen_task(task_id)
    r2 = await storage.get_task(task_id)
    print(f"2. after reopen_task     : {_fmt(r2)}")

    if r2.get("previous_execution_ids"):
        print("   => reopen_task PRESERVES the chain")
    else:
        print("   => reopen_task DROPS the chain (unexpected)")

    # Post-fix contract (orditect v0.1.8+): submit() on a PENDING
    # existing record is schedule-only (no re-initialization). The
    # PENDING state reopen_task left is exactly what submit keys on.
    print()
    print(f"3. pending for submit    : status={r2.get('status')} "
          f"(schedule-only keys on PENDING)")
    print()
    print("Chain-preserving resubmit is verified end-to-end by "
          "examples/acceptance/real_app (hot record prevs=1).")

    await client.delete(f"task:{task_id}")
    await client.aclose()

if __name__ == "__main__":
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    asyncio.run(main(url))
    sys.exit(0)