"""SeededImageEnricher: placeholder pipeline reference implementation.

Migrated from the reference application's publish node. A
MockVectorEnricher with a pre-seeded URL pool: each placeholder
resolves deterministically (digest of the placeholder id, idempotent
across re-renders) to one of the configured URLs.

Latency is intentionally raised above the mock default: the runner
emits enrich.resolved only for placeholders still PENDING when the
settle window opens (fast enrichments settle before the window and are
skipped — they still land in the manifest).

Requires orditect on sys.path.
"""

from __future__ import annotations

import asyncio

from orditect.stream import MockVectorEnricher
from orditect.stream.events import PlaceholderState
from orditect.stream.protocols import EnrichRequest, EnrichResult


class SeededImageEnricher(MockVectorEnricher):
    """Resolve placeholders to pre-seeded URLs, deterministic per id."""

    def __init__(self, urls: tuple[str, ...],
                 latency: float = 2.5) -> None:
        super().__init__(latency=latency)
        self._urls = tuple(urls) or (
            "https://picsum.photos/seed/img/640/360",
        )

    async def resolve(
        self,
        request: EnrichRequest,
        cancel_token=None,
    ) -> EnrichResult:
        await asyncio.sleep(self._latency)
        digest = int.from_bytes(request.placeholder_id.encode()[:4], "little")
        url = self._urls[digest % len(self._urls)]
        return EnrichResult(
            url=url,
            state=PlaceholderState.RESOLVED,
            meta={"source": "seeded"},
        )