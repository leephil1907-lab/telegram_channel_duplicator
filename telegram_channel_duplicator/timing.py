"""Cooperative sleep helper shared by the polling loop."""

from __future__ import annotations

import asyncio
from typing import Optional


async def wait_or_stop(stop_event: Optional[asyncio.Event], delay: float) -> None:
    """Sleep for ``delay`` but wake immediately when a stop is requested."""
    if stop_event is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.01, delay))
    except (asyncio.TimeoutError, TimeoutError):
        pass
