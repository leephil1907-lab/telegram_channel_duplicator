"""Small retry helper with exponential backoff and Telegram FloodWait support."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional


@dataclass
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 1.0
    multiplier: float = 2.0
    max_delay: float = 20.0
    flood_wait_cap: float = 60.0


def describe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def backoff_for(exc: BaseException, attempt: int, policy: RetryPolicy) -> float:
    """Telethon's FloodWaitError carries ``.seconds``; respect the server's hint."""
    seconds = getattr(exc, "seconds", None)
    if isinstance(seconds, (int, float)) and seconds > 0:
        return min(float(seconds) + 0.5, policy.flood_wait_cap)
    jitter = 1.0 + random.random() * 0.15
    return min(policy.base_delay * policy.multiplier ** (attempt - 1) * jitter, policy.max_delay)


async def call_with_retries(
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    policy: Optional[RetryPolicy] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    **kwargs: Any,
) -> Any:
    """Await ``func(*args, **kwargs)``, retrying transient failures.

    ``sleep`` is injectable so tests can run without real waiting.
    """
    policy = policy or RetryPolicy()
    attempts = max(1, policy.attempts)

    for attempt in range(1, attempts + 1):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - network errors are diverse
            if attempt >= attempts:
                raise
            delay = backoff_for(exc, attempt, policy)
            if on_retry:
                on_retry(attempt, exc, delay)
            await sleep(delay)

    raise RuntimeError("unreachable")  # pragma: no cover
