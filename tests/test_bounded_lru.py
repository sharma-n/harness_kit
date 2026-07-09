"""Tests for bounded LRU dict (1.7 — unbounded in-process growth)."""

import pytest

from harness_kit.util import BoundedLRUDict
from harness_kit.tools.ratelimit import ToolRateLimiter


def test_bounded_lru_dict_max_size() -> None:
    """Verify BoundedLRUDict evicts oldest when exceeding capacity."""
    lru: BoundedLRUDict[str, int] = BoundedLRUDict(max_size=3)

    lru["a"] = 1
    lru["b"] = 2
    lru["c"] = 3
    assert len(lru._dict) == 3
    assert "a" in lru
    assert "b" in lru
    assert "c" in lru

    # Adding one more should evict "a" (the oldest)
    lru["d"] = 4
    assert len(lru._dict) == 3
    assert "a" not in lru
    assert "b" in lru
    assert "c" in lru
    assert "d" in lru


def test_bounded_lru_dict_lru_behavior() -> None:
    """Verify BoundedLRUDict respects LRU: accessing a key makes it recent."""
    lru: BoundedLRUDict[str, int] = BoundedLRUDict(max_size=3)

    lru["a"] = 1
    lru["b"] = 2
    lru["c"] = 3
    # Access "a" to make it recent (move to end)
    _ = lru.get("a")

    # Add "d"; should evict "b" (the least recently used)
    lru["d"] = 4
    assert len(lru._dict) == 3
    assert "a" in lru  # most recent
    assert "b" not in lru  # evicted (was least recent)
    assert "c" in lru
    assert "d" in lru


def test_bounded_lru_dict_update_existing_key() -> None:
    """Verify updating an existing key moves it to end (most recent)."""
    lru: BoundedLRUDict[str, int] = BoundedLRUDict(max_size=3)

    lru["a"] = 1
    lru["b"] = 2
    lru["c"] = 3
    # Update "a" to make it recent
    lru["a"] = 10

    # Add "d"; should evict "b" (the least recently used after the update)
    lru["d"] = 4
    assert len(lru._dict) == 3
    assert "a" in lru  # most recent (was just updated)
    assert lru["a"] == 10
    assert "b" not in lru  # evicted
    assert "c" in lru
    assert "d" in lru


def test_tool_rate_limiter_bounded_buckets() -> None:
    """Verify ToolRateLimiter respects max_buckets cap."""
    limiter = ToolRateLimiter(max_buckets=3)

    # Create three buckets
    limiter.try_acquire("user1", "tool_a", 10)
    limiter.try_acquire("user2", "tool_b", 10)
    limiter.try_acquire("user3", "tool_c", 10)
    assert len(limiter._buckets._dict) == 3

    # Creating a fourth bucket should evict the first
    limiter.try_acquire("user4", "tool_d", 10)
    assert len(limiter._buckets._dict) == 3
    assert ("user1", "tool_a") not in limiter._buckets
    assert ("user2", "tool_b") in limiter._buckets
    assert ("user3", "tool_c") in limiter._buckets
    assert ("user4", "tool_d") in limiter._buckets


def test_tool_rate_limiter_reuse_bucket() -> None:
    """Verify re-using a bucket moves it to end (most recent)."""
    limiter = ToolRateLimiter(max_buckets=3)

    limiter.try_acquire("user1", "tool_a", 10)
    limiter.try_acquire("user2", "tool_b", 10)
    limiter.try_acquire("user3", "tool_c", 10)
    # Re-use the first bucket (user1, tool_a)
    limiter.try_acquire("user1", "tool_a", 10)

    # Creating a new bucket should evict (user2, tool_b) now (least recently used)
    limiter.try_acquire("user4", "tool_d", 10)
    assert len(limiter._buckets._dict) == 3
    assert ("user1", "tool_a") in limiter._buckets  # still there, just re-used
    assert ("user2", "tool_b") not in limiter._buckets  # evicted
    assert ("user3", "tool_c") in limiter._buckets
    assert ("user4", "tool_d") in limiter._buckets
