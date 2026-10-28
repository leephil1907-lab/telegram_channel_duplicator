"""Tests for the retry/backoff helper."""

from __future__ import annotations

import asyncio

import pytest

from telegram_channel_duplicator.resilience import (
    RetryPolicy,
    backoff_for,
    call_with_retries,
    describe_error,
)


class FloodWait(Exception):
    def __init__(self, seconds):
        super().__init__(f"flood {seconds}")
        self.seconds = seconds


def test_retries_then_succeeds():
    calls, delays = [], []

    async def flaky(value):
        calls.append(value)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return value * 2

    async def fake_sleep(delay):
        delays.append(delay)

    result = asyncio.run(
        call_with_retries(
            flaky,
            21,
            policy=RetryPolicy(attempts=4, base_delay=0.2, multiplier=2),
            sleep=fake_sleep,
        )
    )
    assert result == 42
    assert calls == [21, 21, 21]
    assert len(delays) == 2 and delays[1] > delays[0]


def test_retry_exhaustion_raises_last_error():
    calls = []

    async def always_fails():
        calls.append(1)
        raise OSError("offline")

    async def fake_sleep(_delay):
        return None

    with pytest.raises(OSError, match="offline"):
        asyncio.run(
            call_with_retries(
                always_fails,
                policy=RetryPolicy(attempts=3, base_delay=0.01),
                sleep=fake_sleep,
            )
        )
    assert len(calls) == 3


def test_retry_callback_is_invoked():
    seen = []

    async def flaky():
        if len(seen) < 2:
            raise ValueError("again")
        return "ok"

    async def fake_sleep(_delay):
        return None

    asyncio.run(
        call_with_retries(
            flaky,
            policy=RetryPolicy(attempts=3, base_delay=0.01),
            on_retry=lambda a, e, d: seen.append((a, str(e), d)),
            sleep=fake_sleep,
        )
    )
    assert len(seen) == 2
    assert seen[0][0] == 1


def test_flood_wait_seconds_are_respected():
    policy = RetryPolicy(base_delay=0.1)
    assert backoff_for(FloodWait(12), 1, policy) == 12.5
    assert backoff_for(FloodWait(999), 1, policy) == policy.flood_wait_cap
    assert backoff_for(ValueError("x"), 3, policy) < 1
    assert "ValueError" in describe_error(ValueError("x"))
