"""In-memory SkillStore — the default per-user skill-visibility backend.

``None`` from ``allowed_skills()`` means "all skills allowed" (v1 default:
every user sees every installed skill). An explicit ``set[str]`` restricts the
user to exactly those skill names. Real adapter: a SQLite table behind the same
Protocol (v2).
"""

from __future__ import annotations


class InMemorySkillStore:
    """Process-local per-user skill allowlists.

    v1 default: ``_default`` is ``None``, meaning all skills are visible to all
    users. Once any call to ``extend_default_allowed`` or ``grant`` is made, the
    store transitions to an explicit set.
    """

    def __init__(self) -> None:
        self._default: set[str] | None = None  # None = all allowed
        self._grants: dict[str, set[str]] = {}

    async def allowed_skills(self, user_id: str) -> set[str] | None:
        if user_id in self._grants:
            return set(self._grants[user_id])
        return self._default  # None in v1 = all allowed

    async def grant(self, user_id: str, skills: set[str]) -> None:
        self._grants.setdefault(user_id, set()).update(skills)

    async def revoke(self, user_id: str, skills: set[str]) -> None:
        if user_id in self._grants:
            self._grants[user_id].difference_update(skills)

    async def extend_default_allowed(self, names: set[str]) -> None:
        # First call transitions from "all allowed" (None) to an explicit set.
        self._default = (self._default or set()) | names
