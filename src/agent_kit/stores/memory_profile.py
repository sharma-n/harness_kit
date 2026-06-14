"""In-memory ProfileStore — the default factual-memory backend."""

from __future__ import annotations

import time

from agent_kit.stores.types import UserProfile


class InMemoryProfileStore:
    """Process-local per-user profiles. Real adapter: SQLite via SQLAlchemy."""

    def __init__(self) -> None:
        self._profiles: dict[str, UserProfile] = {}

    async def get(self, user_id: str) -> UserProfile:
        profile = self._profiles.get(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            self._profiles[user_id] = profile
        return profile

    async def upsert_facts(self, user_id: str, facts: dict) -> None:
        profile = await self.get(user_id)
        profile.facts.update(facts)
        profile.updated_at = time.time()
