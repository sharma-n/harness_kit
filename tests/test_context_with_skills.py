"""Tests for skills block integration in context assembly."""

from __future__ import annotations

from pathlib import Path

from harness_kit.agent.budgeter import ContextBudgeter
from harness_kit.agent.context import ContextBuilder
from harness_kit.config import AgentConfig, ContextConfig
from harness_kit.memory.working import WorkingSnapshot
from harness_kit.skills.loader import SkillMeta
from harness_kit.skills.manager import SkillManager
from harness_kit.stores.memory_skills import InMemorySkillStore
from harness_kit.stores.types import Turn, UserProfile


class _StubWorking:
    async def load(self, conversation_id: str, user_id: str) -> WorkingSnapshot:
        return WorkingSnapshot(buffer=[], summary="")


class _StubFactual:
    async def get(self, user_id: str) -> UserProfile:
        return UserProfile(user_id=user_id)


class _StubEpisodic:
    async def retrieve(self, user_id, message, recent_turns) -> list:
        return []


class _StubRegistry:
    async def definitions(self, user_id) -> list:
        return []


def _make_meta(name: str, description: str, tmp_path: Path) -> SkillMeta:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nBody.\n",
        encoding="utf-8",
    )
    return SkillMeta(
        name=name,
        description=description,
        allowed_tools=[],
        path=skill_dir / "SKILL.md",
        skill_dir=skill_dir,
    )


def _builder(skill_manager=None, skill_store=None) -> ContextBuilder:
    return ContextBuilder(
        agent_cfg=AgentConfig(system_prompt="Base."),
        working=_StubWorking(),
        episodic=_StubEpisodic(),
        factual=_StubFactual(),
        registry=_StubRegistry(),
        budgeter=ContextBudgeter(ContextConfig()),
        skill_manager=skill_manager,
        skill_store=skill_store,
    )


async def test_no_skills_block_when_manager_is_none():
    builder = _builder()
    ctx = await builder.build("user1", "c1", "hello")
    system = ctx.messages[0].text
    assert system == "Base."


async def test_skills_block_appears_in_system_message(tmp_path):
    greet = _make_meta("greet", "Greet users.", tmp_path)
    manager = SkillManager([greet])
    store = InMemorySkillStore()
    builder = _builder(skill_manager=manager, skill_store=store)

    ctx = await builder.build("user1", "c1", "hello")
    system = ctx.messages[0].text
    assert "Available skills" in system
    assert "greet: Greet users." in system


async def test_skills_block_position_after_dynamic_before_factual(tmp_path):
    """Skills block must appear: base_prompt → dynamic → skills_block → factual → …"""

    async def inject(user_id: str, convo_id: str) -> str:
        return "Dynamic text."

    greet = _make_meta("greet", "Greet users.", tmp_path)
    manager = SkillManager([greet])
    store = InMemorySkillStore()

    class FactualWithProfile:
        async def get(self, user_id: str) -> UserProfile:
            return UserProfile(user_id=user_id, facts={"name": "Sam"})

    builder = ContextBuilder(
        agent_cfg=AgentConfig(
            system_prompt="Base.",
            factual_block_header="Facts:",
        ),
        working=_StubWorking(),
        episodic=_StubEpisodic(),
        factual=FactualWithProfile(),
        registry=_StubRegistry(),
        budgeter=ContextBudgeter(ContextConfig()),
        system_prompt_fn=inject,
        skill_manager=manager,
        skill_store=store,
    )

    ctx = await builder.build("user1", "c1", "hello")
    system = ctx.messages[0].text

    # Check order by substring positions
    pos_base = system.index("Base.")
    pos_dynamic = system.index("Dynamic text.")
    pos_skills = system.index("Available skills")
    pos_facts = system.index("Facts:")

    assert pos_base < pos_dynamic < pos_skills < pos_facts


async def test_skills_block_respects_allowed_set(tmp_path):
    greet = _make_meta("greet", "Greet users.", tmp_path)
    summarize = _make_meta("summarize", "Summarize text.", tmp_path)
    manager = SkillManager([greet, summarize])
    store = InMemorySkillStore()
    # Restrict user1 to only "greet"
    await store.grant("user1", {"greet"})

    builder = _builder(skill_manager=manager, skill_store=store)
    ctx = await builder.build("user1", "c1", "hello")
    system = ctx.messages[0].text

    assert "greet" in system
    assert "summarize" not in system


async def test_empty_skills_block_omitted(tmp_path):
    manager = SkillManager([])  # no skills loaded
    store = InMemorySkillStore()
    builder = _builder(skill_manager=manager, skill_store=store)

    ctx = await builder.build("user1", "c1", "hello")
    system = ctx.messages[0].text
    assert system == "Base."
