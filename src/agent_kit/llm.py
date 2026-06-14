"""Thin Protocols over the llm_kit surfaces agent_kit consumes.

Depending on these (not the concrete ``LLMClient`` / ``OpenAICompatibleEmbedder``)
lets the agent loop, memory, and tools be driven by a scripted ``FakeLLM`` in
tests — mirroring llm_kit's own ``FakeLLM`` posture (SPEC §15). The real clients
satisfy these structurally.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from llm_kit import LLMResponse, Message, ToolDefinition
from llm_kit.embed.response import EmbeddingResponse
from llm_kit.llm.streaming import StreamEvent
from pydantic import BaseModel


@runtime_checkable
class LLM(Protocol):
    """Subset of ``llm_kit.LLMClient`` the agent uses."""

    def invoke_stream(
        self,
        messages: list[Message],
        *,
        response_model: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...

    async def invoke(
        self,
        messages: list[Message],
        *,
        response_model: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse: ...


@runtime_checkable
class Embedder(Protocol):
    """Subset of ``llm_kit.OpenAICompatibleEmbedder`` the agent uses."""

    async def embed_one(self, text: str) -> EmbeddingResponse: ...
