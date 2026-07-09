"""In-memory PermissionStore — the default per-user tool-allowlist backend.

Users' allowed tools are computed as a live union and difference from the global
default_allowed set:
  allowed(user_id) = (default ∪ grants[user_id]) − revokes[user_id]

This way, users automatically see future additions to the default set, unless they
explicitly revoke a tool (explicit revoke is a stronger signal than later default
additions). Real adapter: a SQLite table behind the same Protocol.
"""

from __future__ import annotations

from collections.abc import Iterable


class InMemoryPermissionStore:
    """Process-local per-user tool allowlists with a global default fallback."""

    def __init__(self, default_allowed: Iterable[str] = ()) -> None:
        self._default = set(default_allowed)
        # Per-user grants and revokes, computed against the live default at read time.
        self._grants: dict[str, set[str]] = {}
        self._revokes: dict[str, set[str]] = {}

    async def allowed_tools(self, user_id: str) -> set[str]:
        # Union the default with grants, then remove revokes.
        allowed = (self._default | self._grants.get(user_id, set())) - self._revokes.get(user_id, set())
        # Copy so callers can't mutate stored state through the returned set.
        return set(allowed)

    async def grant(self, user_id: str, tools: set[str]) -> None:
        grants = self._grants.setdefault(user_id, set())
        grants.update(tools)
        # Remove from revokes so un-revoking a tool wins.
        revokes = self._revokes.get(user_id, set())
        revokes.difference_update(tools)

    async def revoke(self, user_id: str, tools: set[str]) -> None:
        revokes = self._revokes.setdefault(user_id, set())
        revokes.update(tools)
        # Remove from grants so explicit revoke wins over a stale grant.
        grants = self._grants.get(user_id, set())
        grants.difference_update(tools)

    async def extend_default_allowed(self, names: set[str]) -> None:
        # Grows the fallback set for users without an explicit grant. Called at
        # startup (before any per-user grants exist) for auto_allow MCP servers.
        # Automatically flows to all users except those who explicitly revoked.
        self._default.update(names)
