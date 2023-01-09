#!/usr/bin/env python3
import asyncio
from typing import Any


# From https://stackoverflow.com/a/61478547
async def gather_with_concurrency(max_coros: int, *coros: Any) -> Any:
    semaphore = asyncio.Semaphore(max_coros)

    async def sem_task(coro: Any) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(sem_task(coro) for coro in coros))
