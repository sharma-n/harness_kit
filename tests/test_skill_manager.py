"""Tests for skills/manager.py — SkillManager index and filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_kit.skills.loader import SkillMeta
from agent_kit.skills.manager import SkillManager


def _meta(name: str, description: str, body: str, tmp_path: Path) -> SkillMeta:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )
    return SkillMeta(
        name=name,
        description=description,
        allowed_tools=[],
        path=skill_dir / "SKILL.md",
        skill_dir=skill_dir,
    )


HEADER = "Available skills:"


def test_metadata_block_all_allowed(tmp_path):
    greet = _meta("greet", "Greet users.", "Instructions.", tmp_path)
    summarize = _meta("summarize", "Summarize text.", "Instructions.", tmp_path)
    manager = SkillManager([greet, summarize])
    block = manager.metadata_block(None, HEADER)
    assert block.startswith(HEADER)
    assert "greet: Greet users." in block
    assert "summarize: Summarize text." in block


def test_metadata_block_filtered(tmp_path):
    greet = _meta("greet", "Greet.", "Body.", tmp_path)
    summarize = _meta("summarize", "Summarize.", "Body.", tmp_path)
    manager = SkillManager([greet, summarize])
    block = manager.metadata_block({"greet"}, HEADER)
    assert "greet" in block
    assert "summarize" not in block


def test_metadata_block_empty_when_no_skills():
    manager = SkillManager([])
    assert manager.metadata_block(None, HEADER) == ""


def test_metadata_block_empty_when_none_permitted(tmp_path):
    greet = _meta("greet", "Greet.", "Body.", tmp_path)
    manager = SkillManager([greet])
    block = manager.metadata_block(set(), HEADER)  # empty set → no skills visible
    assert block == ""


def test_metadata_block_sorted_alphabetically(tmp_path):
    zebra = _meta("zebra", "Z skill.", "Body.", tmp_path)
    apple = _meta("apple", "A skill.", "Body.", tmp_path)
    manager = SkillManager([zebra, apple])
    block = manager.metadata_block(None, HEADER)
    lines = block.splitlines()
    names = [line.lstrip("- ").split(":")[0] for line in lines[1:]]
    assert names == sorted(names)


def test_read_body_allowed(tmp_path):
    greet = _meta("greet", "Greet.", "Say hello.", tmp_path)
    manager = SkillManager([greet])
    body = manager.read_body("greet", None)
    assert body is not None
    assert "Say hello." in body


def test_read_body_denied_by_explicit_set(tmp_path):
    greet = _meta("greet", "Greet.", "Say hello.", tmp_path)
    manager = SkillManager([greet])
    result = manager.read_body("greet", {"other-skill"})
    assert result is None


def test_read_body_unknown_name(tmp_path):
    manager = SkillManager([])
    assert manager.read_body("nonexistent", None) is None


def test_list_all(tmp_path):
    greet = _meta("greet", "Greet.", "Body.", tmp_path)
    manager = SkillManager([greet])
    all_skills = manager.list_all()
    assert len(all_skills) == 1
    assert all_skills[0].name == "greet"


def test_get_meta(tmp_path):
    greet = _meta("greet", "Greet.", "Body.", tmp_path)
    manager = SkillManager([greet])
    assert manager.get_meta("greet") is greet
    assert manager.get_meta("missing") is None
