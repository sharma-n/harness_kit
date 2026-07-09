"""Tests for stores/memory_skills.py — InMemorySkillStore."""

from __future__ import annotations

import pytest

from harness_kit.stores.memory_skills import InMemorySkillStore


@pytest.fixture
def store() -> InMemorySkillStore:
    return InMemorySkillStore()


async def test_default_none_means_all_allowed(store):
    result = await store.allowed_skills("user1")
    assert result is None


async def test_grant_creates_explicit_allowlist(store):
    await store.grant("user1", {"greet", "summarize"})
    result = await store.allowed_skills("user1")
    assert result == {"greet", "summarize"}


async def test_grant_is_additive(store):
    await store.grant("user1", {"greet"})
    await store.grant("user1", {"summarize"})
    result = await store.allowed_skills("user1")
    assert result == {"greet", "summarize"}


async def test_revoke_removes_from_set(store):
    await store.grant("user1", {"greet", "summarize"})
    await store.revoke("user1", {"greet"})
    result = await store.allowed_skills("user1")
    assert result == {"summarize"}


async def test_revoke_on_no_grant_is_noop(store):
    await store.revoke("user1", {"greet"})  # should not raise
    result = await store.allowed_skills("user1")
    assert result is None


async def test_extend_default_allowed(store):
    await store.extend_default_allowed({"greet"})
    result = await store.allowed_skills("anyone")
    assert result == {"greet"}


async def test_extend_default_allowed_is_additive(store):
    await store.extend_default_allowed({"greet"})
    await store.extend_default_allowed({"summarize"})
    result = await store.allowed_skills("anyone")
    assert result == {"greet", "summarize"}


async def test_per_user_grant_overrides_default(store):
    await store.extend_default_allowed({"greet"})
    await store.grant("user1", {"summarize"})
    # user1 has explicit grant → use that, not the default
    result = await store.allowed_skills("user1")
    assert result == {"summarize"}
    # user2 has no explicit grant → falls back to default
    result2 = await store.allowed_skills("user2")
    assert result2 == {"greet"}


async def test_grants_are_user_scoped(store):
    await store.grant("user1", {"greet"})
    await store.grant("user2", {"summarize"})
    assert await store.allowed_skills("user1") == {"greet"}
    assert await store.allowed_skills("user2") == {"summarize"}
