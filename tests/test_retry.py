"""Unit tests for the async store-write retry helper (``agent_kit.retry``).

Network-free and clock-free: ``asyncio.sleep`` is monkeypatched to a no-op so the
backoff loop runs instantly while still exercising attempt counting and logging.
"""

from __future__ import annotations

import logging

import pytest

from agent_kit.errors import AgentKitError, StoreWriteError
from agent_kit.retry import RetryPolicy, default_retryable, retry_async, store_write


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr("agent_kit.retry.asyncio.sleep", _instant)


def _counter(fail_times: int, exc: BaseException):
    """A coroutine factory that raises ``exc`` for the first ``fail_times`` calls."""
    state = {"calls": 0}

    async def op():
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise exc
        return "ok"

    return op, state


async def test_succeeds_on_first_attempt_without_sleeping():
    op, state = _counter(fail_times=0, exc=RuntimeError("boom"))
    result = await retry_async(op, policy=RetryPolicy(), operation="t")
    assert result == "ok"
    assert state["calls"] == 1


async def test_retries_then_succeeds():
    op, state = _counter(fail_times=2, exc=ConnectionError("transient"))
    result = await retry_async(
        op, policy=RetryPolicy(max_retries=3), operation="t"
    )
    assert result == "ok"
    assert state["calls"] == 3  # 2 failures + 1 success


async def test_exhaustion_reraises_last_exception():
    boom = ConnectionError("always")
    op, state = _counter(fail_times=99, exc=boom)
    with pytest.raises(ConnectionError):
        await retry_async(op, policy=RetryPolicy(max_retries=2), operation="t")
    assert state["calls"] == 3  # max_retries + 1


async def test_non_retryable_raises_immediately():
    op, state = _counter(fail_times=99, exc=AgentKitError("terminal"))
    with pytest.raises(AgentKitError):
        await retry_async(op, policy=RetryPolicy(max_retries=5), operation="t")
    assert state["calls"] == 1  # AgentKitError is terminal → no retry


async def test_warns_once_per_retry_attempt(caplog):
    op, _ = _counter(fail_times=2, exc=ConnectionError("transient"))
    with caplog.at_level(logging.WARNING, logger="agent_kit.retry"):
        await retry_async(op, policy=RetryPolicy(max_retries=3), operation="myop")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # one per retried failure (not the final success)
    assert all("myop" in r.getMessage() for r in warnings)


def test_default_retryable_classification():
    assert default_retryable(ConnectionError()) is True  # backend transient
    assert default_retryable(AgentKitError()) is False  # our terminal
    import asyncio

    assert default_retryable(asyncio.CancelledError()) is False  # control flow


async def test_store_write_wraps_exhaustion_in_store_write_error():
    op, _ = _counter(fail_times=99, exc=ConnectionError("always"))
    with pytest.raises(StoreWriteError) as exc_info:
        await store_write(op, policy=RetryPolicy(max_retries=1), operation="factual.extract")
    assert exc_info.value.operation == "factual.extract"
    assert isinstance(exc_info.value.__cause__, ConnectionError)


async def test_store_write_does_not_wrap_cancellation():
    import asyncio

    async def op():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await store_write(op, policy=RetryPolicy(), operation="t")
