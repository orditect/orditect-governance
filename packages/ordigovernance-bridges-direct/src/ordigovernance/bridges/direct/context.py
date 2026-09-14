"""Hot-path assembly over Redis (parameters in, governance out)."""

from __future__ import annotations

import redis.asyncio as aioredis

from orditect.core import AdmissionQuotaRedisDB, get_registry
from orditect.flow.governor.factory import TaskbaseGovernorAdapter
from orditect.flow.storage.factory import get_default_storage


async def build_hot_path(redis_url: str, *,
                         semaphores: dict[str, int],
                         lease_time: float = 60.0) -> dict:
    """Create {storage, governor, quota, redis_client, registry}.

    semaphores: {resource_name: limit} registered before first acquire.
    """
    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.ping()

    storage = get_default_storage(client)
    await storage.connect()

    registry = get_registry()
    for name, limit in semaphores.items():
        registry.register_semaphore(name, client, limit=limit,
                                    lease_time=lease_time)

    quota = AdmissionQuotaRedisDB(client=client)
    await quota.connect()

    return {
        "storage": storage,
        "governor": TaskbaseGovernorAdapter(registry),
        "quota": quota,
        "redis_client": client,
        "registry": registry,
    }