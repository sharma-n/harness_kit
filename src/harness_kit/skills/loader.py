"""SKILL.md parser and skill directory discovery (agentskills.io format).

A skill is a directory containing a ``SKILL.md`` file with YAML frontmatter
(``name``, ``description``, optional ``allowed-tools``) followed by Markdown
instructions. Skills live on disk; this module reads them. Nothing is stored in
any database.

Discovery is best-effort: malformed or missing files are logged and skipped,
consistent with MCP's startup posture (failed servers don't crash the service).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"
_FRONTMATTER_DELIMITER = "---"


@dataclass(slots=True)
class SkillMeta:
    """Lightweight metadata loaded at startup for one skill.

    The full instruction body is NOT loaded here — it is read from disk on
    demand when the agent calls ``read_skill``. This keeps startup cheap
    (~50 tokens of name+description per skill in the system message).
    """

    name: str
    description: str
    # Parsed from the 'allowed-tools' frontmatter field.
    # NOT auto-granted — exposed for operator inspection only (see plan).
    allowed_tools: list[str]
    path: Path       # path to the SKILL.md file
    skill_dir: Path  # parent directory (contains scripts/, references/, assets/)


def discover(paths: list[str]) -> list[SkillMeta]:
    """Scan each path for immediate subdirectories that contain a SKILL.md.

    Returns a list of successfully parsed ``SkillMeta`` objects. Errors are
    logged at WARNING level and the skill is skipped — a bad skill never
    prevents the service from starting.
    """
    skills: list[SkillMeta] = []
    for raw_path in paths:
        base = Path(raw_path)
        if not base.exists():
            logger.warning("skills path does not exist, skipping: %s", base)
            continue
        if not base.is_dir():
            logger.warning("skills path is not a directory, skipping: %s", base)
            continue
        for candidate in sorted(base.iterdir()):
            if not candidate.is_dir():
                continue
            meta = load_skill_dir(candidate)
            if meta is not None:
                skills.append(meta)
    return skills


def load_skill_dir(skill_dir: Path) -> SkillMeta | None:
    """Parse one skill directory. Returns ``None`` on missing or invalid SKILL.md."""
    skill_md = skill_dir / SKILL_FILENAME
    if not skill_md.exists():
        return None
    try:
        raw = skill_md.read_text(encoding="utf-8")
        frontmatter, _ = _split_frontmatter(raw, skill_md)
    except Exception as exc:
        logger.warning("failed to read %s: %s", skill_md, exc)
        return None

    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()

    if not name:
        logger.warning("skill at %s has no 'name' field, skipping", skill_dir)
        return None
    if not description:
        logger.warning("skill at %s has no 'description' field, skipping", skill_dir)
        return None

    # The spec requires the name to match the directory name.
    if name != skill_dir.name:
        logger.warning(
            "skill name %r does not match directory name %r in %s",
            name, skill_dir.name, skill_dir,
        )

    allowed_tools_raw = frontmatter.get("allowed-tools", "") or ""
    allowed_tools = str(allowed_tools_raw).split() if allowed_tools_raw else []

    return SkillMeta(
        name=name,
        description=description,
        allowed_tools=allowed_tools,
        path=skill_md,
        skill_dir=skill_dir,
    )


def read_body(skill_meta: SkillMeta) -> str:
    """Read the full SKILL.md body (everything after the frontmatter).

    Reads from disk on each call — no caching. Skills are small files; disk
    reads are fast; and this lets operators update skill files without restart.
    """
    raw = skill_meta.path.read_text(encoding="utf-8")
    _, body = _split_frontmatter(raw, skill_meta.path)
    return body.strip()


def _split_frontmatter(raw: str, source: Path) -> tuple[dict, str]:
    """Split ``---\\nYAML\\n---\\nbody`` into ``(frontmatter_dict, body_text)``.

    Returns an empty dict and the full raw string as body if no frontmatter
    block is found (the spec requires frontmatter, but we degrade gracefully).
    """
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != _FRONTMATTER_DELIMITER:
        return {}, raw

    # Find the closing ---
    close_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == _FRONTMATTER_DELIMITER:
            close_idx = i
            break

    if close_idx is None:
        logger.warning("unclosed frontmatter in %s", source)
        return {}, raw

    fm_text = "".join(lines[1:close_idx])
    body = "".join(lines[close_idx + 1:])

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        logger.warning("invalid YAML frontmatter in %s: %s", source, exc)
        return {}, raw

    if not isinstance(fm, dict):
        logger.warning("frontmatter in %s is not a mapping", source)
        return {}, raw

    return fm, body
