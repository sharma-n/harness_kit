"""In-memory PermissionStore — the default per-user tool-allowlist backend.

Users with no explicit grant fall back to the global ``default_allowed`` set
from config. Real adapter: a SQLite table behind the same Protocol.
"""

from __future__ import annotations

from collections.abc import Iterable


class InMemoryPermissionStore:
    """Process-local per-user tool allowlists with a global default fallback."""

    def __init__(self, default_allowed: Iterable[str] = ()) -> None:
        self._default = set(default_allowed)
        self._grants: dict[str, set[str]] = {}

    async def allowed_tools(self, user_id: str) -> set[str]:
        # Copy so callers can't mutate stored state through the returned set.
        return set(self._grants.get(user_id, self._default))

    async def grant(self, user_id: str, tools: set[str]) -> None:
        current = self._grants.setdefault(user_id, set(self._default))
        current.update(tools)

    async def revoke(self, user_id: str, tools: set[str]) -> None:
        current = self._grants.setdefault(user_id, set(self._default))
        current.difference_update(tools)
