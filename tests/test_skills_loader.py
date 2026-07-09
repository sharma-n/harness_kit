"""Tests for skills/loader.py — SKILL.md discovery and parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_kit.skills.loader import discover, load_skill_dir, read_body


def _write_skill(base: Path, name: str, *, description: str = "A skill.", body: str = "Do the thing.", allowed_tools: str = "") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    at = f"allowed-tools: {allowed_tools}\n" if allowed_tools else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{at}---\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_discover_finds_valid_skill_dirs(tmp_path):
    _write_skill(tmp_path, "greet", description="Greet users.")
    _write_skill(tmp_path, "summarize", description="Summarize text.")
    skills = discover([str(tmp_path)])
    assert {s.name for s in skills} == {"greet", "summarize"}


def test_discover_skips_missing_skill_md(tmp_path):
    empty = tmp_path / "no-skill-file"
    empty.mkdir()
    skills = discover([str(tmp_path)])
    assert skills == []


def test_discover_skips_non_directories(tmp_path):
    (tmp_path / "readme.txt").write_text("ignore me", encoding="utf-8")
    _write_skill(tmp_path, "greet", description="Greet users.")
    skills = discover([str(tmp_path)])
    assert len(skills) == 1
    assert skills[0].name == "greet"


def test_discover_skips_missing_name(tmp_path, caplog):
    skill_dir = tmp_path / "no-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Missing name.\n---\nBody.\n", encoding="utf-8"
    )
    skills = discover([str(tmp_path)])
    assert skills == []
    assert "no 'name' field" in caplog.text


def test_discover_skips_missing_description(tmp_path, caplog):
    skill_dir = tmp_path / "no-desc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: no-desc\n---\nBody.\n", encoding="utf-8"
    )
    skills = discover([str(tmp_path)])
    assert skills == []
    assert "no 'description' field" in caplog.text


def test_discover_skips_malformed_yaml(tmp_path, caplog):
    skill_dir = tmp_path / "bad-yaml"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n: : broken yaml\n---\nBody.\n", encoding="utf-8"
    )
    skills = discover([str(tmp_path)])
    assert skills == []


def test_discover_nonexistent_path_skipped(tmp_path, caplog):
    skills = discover([str(tmp_path / "does-not-exist")])
    assert skills == []
    assert "does not exist" in caplog.text


def test_read_body_strips_frontmatter(tmp_path):
    skill_dir = _write_skill(tmp_path, "greet", description="Greet.", body="## Instructions\nSay hello.")
    meta = load_skill_dir(skill_dir)
    assert meta is not None
    body = read_body(meta)
    assert "## Instructions" in body
    assert "name:" not in body
    assert "description:" not in body


def test_read_body_strips_leading_trailing_whitespace(tmp_path):
    skill_dir = tmp_path / "greet"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: greet\ndescription: Greet.\n---\n\n\nBody here.\n\n", encoding="utf-8"
    )
    meta = load_skill_dir(skill_dir)
    body = read_body(meta)
    assert body == "Body here."


def test_allowed_tools_parsed_from_space_separated(tmp_path):
    skill_dir = _write_skill(
        tmp_path, "coder", description="Write code.", allowed_tools="read_file write_file bash"
    )
    meta = load_skill_dir(skill_dir)
    assert meta is not None
    assert meta.allowed_tools == ["read_file", "write_file", "bash"]


def test_allowed_tools_empty_when_absent(tmp_path):
    skill_dir = _write_skill(tmp_path, "greet", description="Greet.")
    meta = load_skill_dir(skill_dir)
    assert meta is not None
    assert meta.allowed_tools == []


def test_skill_meta_fields(tmp_path):
    skill_dir = _write_skill(tmp_path, "greet", description="Greet users.")
    meta = load_skill_dir(skill_dir)
    assert meta is not None
    assert meta.name == "greet"
    assert meta.description == "Greet users."
    assert meta.path == skill_dir / "SKILL.md"
    assert meta.skill_dir == skill_dir
