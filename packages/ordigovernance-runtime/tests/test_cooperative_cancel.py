import asyncio

import pytest

from ordigovernance.runtime.orchestration.cooperative_cancel import (
    cooperative_delay,
    raise_if_cancelled,
)


class FakeTaskIO:
    def __init__(self):
        self.record = {"execution_id": "exec-1"}

    async def get_task(self, task_id):
        return self.record

    async def update_task(self, task_id, patch):
        self.record.update(patch)


def test_delay_completes_without_cancel():
    io = FakeTaskIO()
    asyncio.run(cooperative_delay(io, "t", 0.03, slice_seconds=0.01))


def test_delay_aborts_on_cancel():
    io = FakeTaskIO()

    async def main():
        async def cancel_soon():
            await asyncio.sleep(0.02)
            io.record["cancel_requested"] = True

        task = asyncio.create_task(cancel_soon())
        with pytest.raises(asyncio.CancelledError):
            await cooperative_delay(io, "t", 10.0, slice_seconds=0.01)
        await task

    asyncio.run(main())


def test_raise_if_cancelled():
    io = FakeTaskIO()
    asyncio.run(raise_if_cancelled(io, "t"))  # no request: returns
    io.record["cancel_requested"] = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(raise_if_cancelled(io, "t"))