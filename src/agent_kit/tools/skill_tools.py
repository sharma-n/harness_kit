"""Native tool: read_skill — progressive disclosure for agentskills.io skills.

The agent calls this to load the full SKILL.md body for a named skill.
Permission is re-checked at execution time (defense-in-depth mirror of
ToolRegistry.execute(), which re-checks PermissionStore before calling any handler).
"""

from __future__ import annotations

from llm_kit import ToolDefinition

from agent_kit.skills.manager import SkillManager
from agent_kit.stores.base import SkillStore
from agent_kit.tools.base import Tool


def read_skill_tool(skill_manager: SkillManager, skill_store: SkillStore) -> Tool:
    async def handler(user_id: str, args: dict) -> str:
        name = str(args.get("name", "")).strip()
        if not name:
            return "error: 'name' is required"
        allowed = await skill_store.allowed_skills(user_id)
        body = skill_manager.read_body(name, allowed)
        if body is None:
            visible = sorted(
                s.name for s in skill_manager.list_all()
                if allowed is None or s.name in allowed
            )
            listed = ", ".join(visible) if visible else "none"
            return f"skill {name!r} not found. Available: {listed}"
        return body

    return Tool(
        definition=ToolDefinition(
            name="read_skill",
            description=(
                "Load the full instructions for an available skill by name. "
                "Call this when a task matches a skill's description."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name exactly as listed.",
                    }
                },
                "required": ["name"],
            },
        ),
        handler=handler,
    )
