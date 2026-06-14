"""Test doubles + fixtures, mirroring llm_kit's FakeLLM posture (SPEC §15).

``FakeLLM.invoke_stream`` replays a scripted sequence of turns; each turn is a
list of text chunks followed by a ``StreamEnd`` carrying scripted tool calls.
This lets us assert exact ``AgentEvent`` sequences, the ``max_iterations`` cap,
and tool-error-as-observation without any network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from llm_kit import LLMResponse, Message, StreamEnd, TextChunk, ToolCall, ToolDefinition
from llm_kit.embed.response import EmbeddingResponse
from llm_kit.llm.response import TokenUsage
from llm_kit.llm.streaming import StreamEvent
from pydantic import BaseModel

from agent_kit.config import AgentKitConfig
from agent_kit.service import AgentService


class ScriptedTurn:
    """One streamed model response: text chunks + final tool calls/usage."""

    def __init__(
        self,
        text_chunks: list[str] | None = None,
        tool_calls: list[ToolCall] | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        self.text_chunks = text_chunks or []
        self.tool_calls = tool_calls or []
        self.usage = usage or TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


class FakeLLM:
    """Replays scripted turns. ``invoke`` returns a scripted parsed object."""

    def __init__(
        self,
        turns: list[ScriptedTurn] | None = None,
        invoke_parsed: BaseModel | None = None,
    ) -> None:
        self._turns = list(turns or [])
        self._cursor = 0
        self._invoke_parsed = invoke_parsed
        self.stream_calls: list[list[Message]] = []

    async def invoke_stream(
        self,
        messages: list[Message],
        *,
        response_model: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice=None,
    ) -> AsyncIterator[StreamEvent]:
        self.stream_calls.append(list(messages))
        if self._cursor < len(self._turns):
            turn = self._turns[self._cursor]
        else:
            turn = ScriptedTurn(text_chunks=["(no more script)"])
        self._cursor += 1

        text = ""
        for chunk in turn.text_chunks:
            text += chunk
            yield TextChunk(chunk)
        yield StreamEnd(
            LLMResponse(
                text=text,
                usage=turn.usage,
                finish_reason="stop",
                tool_calls=list(turn.tool_calls),
            )
        )

    async def invoke(
        self,
        messages: list[Message],
        *,
        response_model: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        return LLMResponse(text="", parsed=self._invoke_parsed)


class FakeEmbedder:
    """Deterministic toy embeddings: a fixed-dim vector seeded by text hash."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    async def embed_one(self, text: str) -> EmbeddingResponse:
        vec = [0.0] * self._dim
        for i, ch in enumerate(text):
            vec[i % self._dim] += (ord(ch) % 13) / 13.0
        return EmbeddingResponse(
            vector=vec, index=0, usage=TokenUsage(), model="fake-embed"
        )


@pytest.fixture
def base_config() -> AgentKitConfig:
    return AgentKitConfig()  # all defaults → in-memory stores


def make_service(
    cfg: AgentKitConfig,
    turns: list[ScriptedTurn] | None = None,
    *,
    invoke_parsed: BaseModel | None = None,
    extra_tools=None,
) -> tuple[AgentService, FakeLLM]:
    llm = FakeLLM(turns=turns, invoke_parsed=invoke_parsed)
    service = AgentService.build(
        cfg, llm=llm, embedder=FakeEmbedder(), extra_tools=extra_tools
    )
    return service, llm


def tc(call_id: str, name: str, **arguments) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)
