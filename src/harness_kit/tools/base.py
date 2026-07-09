"""Tool abstractions shared by native and (future) MCP tools.

A ``Tool`` couples a provider-agnostic ``ToolDefinition`` (rendered into the
provider tool slot by llm_kit's formatter) with an async handler. Handlers
receive ``user_id`` so a tool can act on the calling user's memory/permissions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from llm_kit import ToolDefinition

# A handler takes (user_id, arguments) and returns the observation string.
ToolHandler = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(slots=True)
class Tool:
    definition: ToolDefinition
    handler: ToolHandler

    @property
    def name(self) -> str:
        return self.definition.name
