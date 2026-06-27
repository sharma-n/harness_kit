"""Live tests: skill discovery and agent-driven skill loading.

A temporary SKILL.md is written to a tmp_path directory and the service is
configured to scan it. Tests verify the skill is discovered at startup, the
read_skill tool is in the registry, and the agent follows verbatim instructions
from the skill body.
"""

from __future__ import annotations

import pytest

from agent_kit.service import AgentService
from agent_kit.agent.events import TextDelta, ToolCallStarted
from tests.conftest import FakeEmbedder
from tests.integration.conftest import requires_live, _load_live_cfg, run_turn

pytestmark = requires_live

USER_ID = "live-test-user-skills"

_SKILL_BODY = (
    "---\n"
    "name: greet_user\n"
    "description: Instructions for greeting a user by name and city.\n"
    "---\n"
    "## Greeting Protocol\n\n"
    "When asked to greet someone:\n"
    "1. Address them by their first name.\n"
    "2. Mention their city.\n"
    "3. End your response with the exact phrase: 'Welcome aboard!'\n"
)


@pytest.fixture
def skill_dir(tmp_path):
    d = tmp_path / "greet_user"
    d.mkdir()
    (d / "SKILL.md").write_text(_SKILL_BODY, encoding="utf-8")
    return tmp_path


@pytest.fixture
async def live_service_with_skills(skill_dir):
    cfg = _load_live_cfg(skills={"paths": [str(skill_dir)]})
    service = AgentService.build(cfg, embedder=FakeEmbedder(dim=8))
    await service.astart()
    yield service
    await service.aclose()


async def test_skill_discovered_at_startup(live_service_with_skills):
    assert live_service_with_skills.skill_manager is not None
    skills = live_service_with_skills.skill_manager.list_all()
    assert len(skills) >= 1
    assert any(s.name == "greet_user" for s in skills)


async def test_read_skill_in_registry(live_service_with_skills, conv_id):
    definitions = await live_service_with_skills.registry.definitions(USER_ID)
    names = {d.name for d in definitions}
    assert "read_skill" in names, f"read_skill missing from registry; got {names}"


async def test_agent_calls_read_skill_and_follows_instructions(
    live_service_with_skills, conv_id
):
    events = await run_turn(
        live_service_with_skills,
        USER_ID,
        conv_id,
        "Please greet Alice who lives in Paris. Use the greet_user skill for this.",
    )

    tool_names = [e.name for e in events if isinstance(e, ToolCallStarted)]
    assert "read_skill" in tool_names, (
        f"expected read_skill to be called, got {tool_names}"
    )

    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "Welcome aboard!" in text, (
        f"expected verbatim phrase 'Welcome aboard!' from skill instructions in response:\n{text}"
    )
