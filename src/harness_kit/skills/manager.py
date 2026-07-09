"""SkillManager — in-memory index and query interface for discovered skills.

``SkillManager`` is constructed once at startup from the list returned by
``loader.discover()``. It provides two operations used at request time:

- ``metadata_block(allowed, header)``: lightweight system-message block listing
  visible skills (name + description only, ~50 tokens/skill).
- ``read_body(name, allowed)``: full SKILL.md body text for the ``read_skill``
  tool, read from disk on demand.
"""

from __future__ import annotations

from harness_kit.skills.loader import SkillMeta, read_body


class SkillManager:
    """In-memory index of discovered skills, with per-user visibility filtering."""

    def __init__(self, skills: list[SkillMeta]) -> None:
        # Preserve discovery order within each path; sort for determinism overall.
        self._index: dict[str, SkillMeta] = {s.name: s for s in skills}

    # ------------------------------------------------------------------
    # Context-assembly surface
    # ------------------------------------------------------------------

    def metadata_block(self, allowed: set[str] | None, header: str) -> str:
        """Return the lightweight skills block for the system message.

        ``allowed=None`` means all skills are visible (v1 global default).
        ``allowed=set[str]`` restricts to exactly those names.
        Returns an empty string when no skills are visible — callers must
        check for empty before including in the system message.

        Skills are listed alphabetically for deterministic context assembly
        (ordering must not vary between requests to allow golden tests).
        """
        visible = [
            meta
            for name, meta in sorted(self._index.items())
            if allowed is None or name in allowed
        ]
        if not visible:
            return ""
        lines = [header]
        for meta in visible:
            lines.append(f"- {meta.name}: {meta.description}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # read_skill tool surface
    # ------------------------------------------------------------------

    def read_body(self, name: str, allowed: set[str] | None) -> str | None:
        """Return the full SKILL.md body if the skill exists and is visible.

        ``allowed=None`` means the user can read any skill.
        Returns ``None`` when the skill is not found or the user is not
        permitted (defense-in-depth mirror of the metadata_block filter).
        Reads the file from disk on each call — no in-process body cache.
        """
        meta = self._index.get(name)
        if meta is None:
            return None
        if allowed is not None and name not in allowed:
            return None
        return read_body(meta)

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def get_meta(self, name: str) -> SkillMeta | None:
        return self._index.get(name)

    def list_all(self) -> list[SkillMeta]:
        return list(self._index.values())
