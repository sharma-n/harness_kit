"""Tests for tools/skill_tools.py — read_skill tool handler."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_kit.skills.loader import SkillMeta
from harness_kit.skills.manager import SkillManager
from harness_kit.stores.memory_skills import InMemorySkillStore
from harness_kit.tools.skill_tools import read_skill_tool


def _meta(name: str, body: str, tmp_path: Path) -> SkillMeta:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}\n",
        encoding="utf-8",
    )
    return SkillMeta(
        name=name,
        description="A skill.",
        allowed_tools=[],
        path=skill_dir / "SKILL.md",
        skill_dir=skill_dir,
    )


async def test_read_skill_returns_body(tmp_path):
    greet = _meta("greet", "Say hello to the user.", tmp_path)
    manager = SkillManager([greet])
    store = InMemorySkillStore()
    tool = read_skill_tool(manager, store)

    result = await tool.handler("user1", {"name": "greet"})
    assert "Say hello to the user." in result


async def test_read_skill_permission_denied(tmp_path):
    greet = _meta("greet", "Say hello.", tmp_path)
    manager = SkillManager([greet])
    store = InMemorySkillStore()
    # Grant only "other", not "greet"
    await store.grant("user1", {"other"})

    tool = read_skill_tool(manager, store)
    result = await tool.handler("user1", {"name": "greet"})
    assert "not found" in result


async def test_read_skill_unknown_name_lists_available(tmp_path):
    greet = _meta("greet", "Say hello.", tmp_path)
    manager = SkillManager([greet])
    store = InMemorySkillStore()
    tool = read_skill_tool(manager, store)

    result = await tool.handler("user1", {"name": "nonexistent"})
    assert "not found" in result
    assert "greet" in result


async def test_read_skill_missing_name_arg(tmp_path):
    manager = SkillManager([])
    store = InMemorySkillStore()
    tool = read_skill_tool(manager, store)

    result = await tool.handler("user1", {})
    assert "error" in result
    assert "'name' is required" in result


async def test_read_skill_empty_name_arg(tmp_path):
    manager = SkillManager([])
    store = InMemorySkillStore()
    tool = read_skill_tool(manager, store)

    result = await tool.handler("user1", {"name": "   "})
    assert "error" in result
    assert "'name' is required" in result


async def test_read_skill_lists_none_when_no_skills(tmp_path):
    manager = SkillManager([])
    store = InMemorySkillStore()
    tool = read_skill_tool(manager, store)

    result = await tool.handler("user1", {"name": "greet"})
    assert "none" in result


def test_read_skill_tool_definition():
    manager = SkillManager([])
    store = InMemorySkillStore()
    tool = read_skill_tool(manager, store)
    assert tool.definition.name == "read_skill"
    assert "name" in tool.definition.parameters["properties"]
    assert "name" in tool.definition.parameters["required"]
