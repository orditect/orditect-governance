"""Shared test helpers."""

import asyncio


def run(coro):
    """Drive one coroutine to completion on a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()