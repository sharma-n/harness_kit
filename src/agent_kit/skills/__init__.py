"""Skills layer — agentskills.io compatible file-based capability extensions."""

from agent_kit.skills.loader import SkillMeta, discover, load_skill_dir
from agent_kit.skills.manager import SkillManager

__all__ = ["SkillMeta", "SkillManager", "discover", "load_skill_dir"]
