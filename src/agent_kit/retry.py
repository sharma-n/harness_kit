"""Async retry for background store writes — a leaf utility shared across layers.

Lives at the package root (like ``tokens`` / ``errors`` / ``llm``) so ``memory/``
can retry its store writes without importing a higher layer, and without naming any
backend exception type (a redis/sqlite/qdrant import would leak a backend into
agent_kit's lower layers and break the layering rule).

Scope: this retries **plain store writes only**. The LLM ``invoke`` and embedder
calls that precede those writes are already retried by llm_kit's own ``http.llm_retry``
infrastructure — re-wrapping them here would re-run an expensive, already-succeeded
model call on a store failure. Callers therefore pass a zero-arg coroutine factory
that closes over the store call alone.

Mirrors llm_kit's ``http/retry.py`` style: exponential backoff (``base * 2**attempt``)
plus uniform jitter, capped at ``backoff_max_seconds``; one WARNING per retry attempt.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from agent_kit.errors import AgentKitError, StoreWriteError

try:  # llm_kit is a hard dependency, but keep the leaf importable without it.
    from llm_kit.errors import LLMError
except Exception:  # pragma: no cover - defensive
    LLMError = ()  # type: ignore[assignment]

T = TypeVar("T")

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetryPolicy:
    """Backoff knobs for background store writes (see ``config.StoreRetryConfig``)."""

    max_retries: int = 3
    backoff_base_seconds: float = 0.2
    backoff_max_seconds: float = 5.0
    jitter_seconds: float = 0.1


def default_retryable(exc: BaseException) -> bool:
    """Retry transient backend faults; never retry terminal/control-flow errors.

    agent_kit cannot name backend exception types (redis/sqlite/qdrant) without
    breaking the layering rule, so the predicate inverts the question: anything that
    is *not* a known-terminal error is treated as a transient backend fault and
    retried. Terminal: control-flow exceptions, agent_kit's own semantic errors, and
    llm_kit errors flagged ``retryable=False`` (defensive — these are already retried
    upstream and should not normally reach a store write).
    """
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        return False
    if isinstance(exc, AgentKitError):
        return False
    if LLMError and isinstance(exc, LLMError):
        return bool(exc.retryable)
    return True


def _backoff_delay(attempt: int, policy: RetryPolicy) -> float:
    base_exp = policy.backoff_base_seconds * (2**attempt)
    jitter = random.uniform(0.0, policy.jitter_seconds)
    return min(base_exp + jitter, policy.backoff_max_seconds)


async def retry_async(
    op: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    operation: str,
    is_retryable: Callable[[BaseException], bool] = default_retryable,
    logger: logging.Logger = _logger,
) -> T:
    """Run ``op`` with up to ``policy.max_retries`` retries on transient failures.

    ``op`` is a zero-arg coroutine factory so each attempt re-issues only the wrapped
    store call. The last exception is re-raised on exhaustion; the caller (memory
    layer) wraps it in ``StoreWriteError`` and the agent-loop choke point logs the
    terminal failure once with full context.
    """
    last_exc: BaseException | None = None
    for attempt in range(policy.max_retries + 1):
        try:
            return await op()
        except BaseException as exc:  # noqa: BLE001 - re-raised below if not retryable
            last_exc = exc
            if attempt >= policy.max_retries or not is_retryable(exc):
                raise
            delay = _backoff_delay(attempt, policy)
            # M9: a retry-attempt metric/span hook attaches here.
            logger.warning(
                "store write %s failed (attempt %d/%d), retrying in %.2fs: %r",
                operation,
                attempt + 1,
                policy.max_retries + 1,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    # Unreachable: the loop either returns or raises. Satisfies type-checkers.
    raise last_exc  # type: ignore[misc]  # pragma: no cover


async def store_write(
    op: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    operation: str,
) -> T:
    """Run a background store write with retry, surfacing exhaustion as a typed error.

    The memory layer calls this for its plain store writes (``upsert_facts``, ``save``,
    ``add``, ``mark_finalized``). On exhaustion the backend exception is re-raised as
    ``StoreWriteError`` (chained), so the agent-loop choke point logs one self-describing
    line that distinguishes a store-write failure from an upstream LLM-step failure.
    Control-flow exceptions propagate untouched so cancellation/shutdown still works.
    """
    try:
        return await retry_async(op, policy=policy, operation=operation)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise StoreWriteError(operation) from exc
