"""Thin Protocols over the llm_kit surfaces agent_kit consumes.

Depending on these (not the concrete ``LLMClient`` / ``OpenAICompatibleEmbedder``)
lets the agent loop, memory, and tools be driven by a scripted ``FakeLLM`` in
tests — mirroring llm_kit's own ``FakeLLM`` posture (SPEC §15). The real clients
satisfy these structurally.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from llm_kit import LLMResponse, Message, StreamEnd, ToolDefinition
from llm_kit.embed.response import EmbeddingResponse
from llm_kit.llm.streaming import StreamEvent
from pydantic import BaseModel

from agent_kit import telemetry


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


def _response_output(resp: LLMResponse) -> Any:
    """Compact view of an LLM response for a generation's ``output`` (prompt/completion
    are the point of LLM tracing). Tool-call turns record the requested tool names."""
    text = getattr(resp, "text", None)
    tool_calls = getattr(resp, "tool_calls", None)
    if tool_calls:
        return {"text": text, "tool_calls": [getattr(c, "name", str(c)) for c in tool_calls]}
    return text


class TracingLLM:
    """Wraps an ``LLM`` so every call becomes a Langfuse *generation* (model + token
    usage → cost). Structurally still an ``LLM``; a pure pass-through (no stream
    buffering, TTFT preserved) and a no-op when telemetry is disabled."""

    def __init__(self, inner: LLM, *, model: str | None = None) -> None:
        self._inner = inner
        self._model = model

    async def invoke_stream(
        self,
        messages: list[Message],
        *,
        response_model: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if not telemetry.is_enabled():
            async for event in self._inner.invoke_stream(
                messages, response_model=response_model, tools=tools, tool_choice=tool_choice
            ):
                yield event
            return

        # Snapshot the prompt: the caller mutates ``messages`` after this returns, and
        # Langfuse serializes ``input`` lazily at export time.
        gen = telemetry.start_generation("llm.invoke_stream", input=list(messages))
        response: LLMResponse | None = None
        try:
            async for event in self._inner.invoke_stream(
                messages, response_model=response_model, tools=tools, tool_choice=tool_choice
            ):
                if isinstance(event, StreamEnd):
                    response = event.response
                yield event
        except BaseException as exc:
            gen.set_error(exc)
            raise
        finally:
            if response is not None:
                gen.record_generation(
                    model=getattr(response, "model", None) or self._model,
                    usage=getattr(response, "usage", None),
                    output=_response_output(response),
                )
            gen.end()

    async def invoke(
        self,
        messages: list[Message],
        *,
        response_model: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        with telemetry.span("llm.invoke", kind="generation", input=list(messages)) as gen:
            resp = await self._inner.invoke(
                messages, response_model=response_model, tools=tools
            )
            gen.record_generation(
                model=getattr(resp, "model", None) or self._model,
                usage=getattr(resp, "usage", None),
                output=_response_output(resp),
            )
            return resp


class TracingEmbedder:
    """Wraps an ``Embedder`` so each ``embed_one`` becomes a Langfuse *embedding*
    observation. No-op when telemetry is disabled."""

    def __init__(self, inner: Embedder, *, model: str | None = None) -> None:
        self._inner = inner
        self._model = model

    async def embed_one(self, text: str) -> EmbeddingResponse:
        with telemetry.span("embed_one", kind="embedding", input=text) as gen:
            resp = await self._inner.embed_one(text)
            gen.record_generation(model=self._model, usage=getattr(resp, "usage", None))
            return resp
