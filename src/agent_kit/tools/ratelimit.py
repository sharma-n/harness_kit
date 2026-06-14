"""Per-tool, per-user rate limiting (SPEC §8 / M10).

A small **in-process**, **reject-on-exceed** token bucket keyed by
``(user_id, tool_name)``. The algorithm is the classic refilling token bucket — the
same one ``llm_kit.rate_limit.limiter.TokenBucket`` uses — but with two deliberate
differences for a *tool gate*:

  - **Non-blocking.** ``llm_kit``'s ``TokenBucket.acquire`` *waits* until tokens are
    free (right for throttling outbound LLM traffic). A tool gate must instead reject
    immediately so a rate-limited call becomes a ``ToolResult(ok=False)`` observation
    fed back to the model — never a stall, because time-to-first-token is agent_kit's
    whole identity.
  - **Per-(user, tool).** Buckets are created lazily, one per caller-and-tool, so a
    high-value tool can be rate-limited per user without affecting anyone else.

Scaling caveat (same as ``llm_kit``'s own limiter): buckets live in process memory,
so in a multi-worker deployment each worker enforces the limit independently — the
effective ceiling is roughly ``workers × rate_limit_per_minute``. A shared-store
(Redis) backing is a later scaling step, not needed for the reference implementation.
"""

from __future__ import annotations

import time


class _Bucket:
    """One refilling token bucket: ``capacity`` tokens, refilled per minute.

    Mirrors ``llm_kit.rate_limit.limiter.TokenBucket``'s refill math (``monotonic``
    clock so it needs no running loop and ignores wall-clock jumps), minus the async
    lock — ``try_consume`` is a synchronous, non-awaiting check, and the agent loop is
    single-threaded per turn, so there is no contention to guard against.
    """

    __slots__ = ("_capacity", "_refill_per_second", "_tokens", "_last_refill")

    def __init__(self, capacity_per_minute: float) -> None:
        self._capacity = float(capacity_per_minute)
        self._refill_per_second = self._capacity / 60.0
        self._tokens = self._capacity
        self._last_refill = time.monotonic()

    def try_consume(self, amount: float = 1.0) -> bool:
        """Take ``amount`` tokens if available; return whether it succeeded."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._refill_per_second
            )
            self._last_refill = now
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False


class ToolRateLimiter:
    """Lazily-created per-``(user_id, tool_name)`` token buckets."""

    __slots__ = ("_buckets",)

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def try_acquire(self, user_id: str, tool: str, per_minute: int) -> bool:
        """Return ``True`` if this user may invoke ``tool`` now, else ``False``.

        ``per_minute`` is both the steady rate and the burst capacity. A non-positive
        limit is treated as "no allowance" (always rejects); ``None`` should be handled
        by the caller (no limiter call at all → unlimited).
        """
        if per_minute <= 0:
            return False
        key = (user_id, tool)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(per_minute)
            self._buckets[key] = bucket
        return bucket.try_consume()
