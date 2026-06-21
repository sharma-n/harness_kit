"""The agent loop (SPEC §5): context → invoke_stream → tool exec → repeat.

``run_turn`` yields a typed ``AgentEvent`` stream. The loop drives tool execution
off ``StreamEnd.response.tool_calls`` (the fully-assembled calls with parsed
arguments) — the mid-stream ``ToolCallStarted`` from llm_kit is name-only and is
not used for execution.

Safety rails (SPEC §5):
  - ``max_iterations`` cap → graceful stop, never an infinite loop.
  - Tool errors are observations: a failed/denied/timed-out tool yields
    ``ToolResult(ok=False)`` fed back to the model, never raised.
  - Optional per-turn wall-clock budget.

After the loop, the completed turns are appended to working memory (synchronous)
and episodic/factual writes are enqueued off the hot path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from llm_kit import Message, StreamEnd, TextChunk
from llm_kit.llm.response import TokenUsage

from agent_kit.agent.context import ContextBuilder
from agent_kit.agent.events import (
    AgentEvent,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnComplete,
)
from agent_kit.config import AgentConfig
from agent_kit.errors import UnauthorizedError
from agent_kit.llm import LLM
from agent_kit.memory.episodic import EpisodicMemory
from agent_kit.memory.factual import FactualMemory
from agent_kit.memory.working import WorkingMemory
from agent_kit.stores.types import Turn
from agent_kit.tools.registry import ToolRegistry
from agent_kit import telemetry

logger = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        llm: LLM,
        context_builder: ContextBuilder,
        registry: ToolRegistry,
        working: WorkingMemory,
        episodic: EpisodicMemory,
        factual: FactualMemory,
        cfg: AgentConfig,
    ) -> None:
        self._llm = llm
        self._context = context_builder
        self._registry = registry
        self._working = working
        self._episodic = episodic
        self._factual = factual
        self._cfg = cfg
        self._bg_tasks: set[asyncio.Task] = set()

    async def run_turn(
        self, user_id: str, conversation_id: str, user_message: str
    ) -> AsyncIterator[AgentEvent]:
        # The root span for the whole turn. ``conversation_id`` becomes the Langfuse
        # session and ``user_id`` the Langfuse user, so every child span (context
        # build, LLM generations, tool calls) and the background writes enqueued below
        # — still inside this ``with`` — land in one trace, queryable per conversation.
        with telemetry.turn_span(
            "turn",
            user_id=user_id,
            conversation_id=conversation_id,
            input=user_message,
        ) as turn:
            ctx = await self._context.build(user_id, conversation_id, user_message)
            messages = list(ctx.messages)

            usage = TokenUsage()
            iterations = 0
            stop_reason = "completed"
            assistant_texts: list[str] = []

            deadline = (
                asyncio.get_event_loop().time() + self._cfg.per_turn_budget_s
                if self._cfg.per_turn_budget_s
                else None
            )

            for iterations in range(1, self._cfg.max_iterations + 1):
                if deadline is not None and asyncio.get_event_loop().time() > deadline:
                    stop_reason = "turn_budget_exceeded"
                    break

                response = None
                async for event in self._llm.invoke_stream(messages, tools=ctx.tools):
                    if isinstance(event, TextChunk):
                        if event.text:
                            yield TextDelta(event.text)
                    elif isinstance(event, StreamEnd):
                        response = event.response

                if response is None:  # stream produced no terminal — treat as done
                    stop_reason = "no_response"
                    break

                usage = _accumulate(usage, response.usage)
                if response.text:
                    assistant_texts.append(response.text)

                tool_calls = response.tool_calls
                if not tool_calls:
                    stop_reason = "completed"
                    break  # the model answered

                # Replay the assistant's tool-call turn, then each observation.
                messages.append(
                    Message.assistant_tool_calls(tool_calls, text=response.text or None)
                )
                for call in tool_calls:
                    yield ToolCallStarted(
                        call_id=call.id, name=call.name, arguments=call.arguments
                    )
                    execution = await self._registry.execute(user_id, call)
                    yield ToolResult(
                        call_id=execution.call_id,
                        name=execution.name,
                        ok=execution.ok,
                        content=execution.display,
                    )
                    messages.append(Message.tool_result(call.id, execution.observation))
            else:
                # for-loop exhausted without break → hit the iteration cap.
                stop_reason = "max_iterations"

            await self._persist(user_id, conversation_id, user_message, assistant_texts)
            turn.set_attributes(
                iterations=iterations,
                stop_reason=stop_reason,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            )
            yield TurnComplete(usage=usage, iterations=iterations, stop_reason=stop_reason)

    async def _persist(
        self,
        user_id: str,
        conversation_id: str,
        user_message: str,
        assistant_texts: list[str],
    ) -> None:
        # Synchronous, hot-path write to working memory.
        await self._working.append_turn(conversation_id, Turn(role="user", text=user_message))
        final_text = "\n".join(assistant_texts).strip()
        if final_text:
            await self._working.append_turn(
                conversation_id, Turn(role="assistant", text=final_text)
            )

        # Off the hot path: factual extraction + token-budget-driven rollover of the
        # working buffer into the rolling summary. Episodic embedding is deferred to
        # conversation end (see ``end_conversation``), not written per turn.
        if final_text:
            self._enqueue(
                self._factual.extract(user_id, user_message, final_text),
                operation="factual.extract",
                user_id=user_id,
                conversation_id=conversation_id,
            )
        self._enqueue(
            self._working.maybe_rollover(conversation_id, user_id),
            operation="working.rollover",
            user_id=user_id,
            conversation_id=conversation_id,
        )

    async def end_conversation(self, user_id: str, conversation_id: str) -> None:
        """Embed the whole conversation as one episodic point and mark it finalized.

        Called when a conversation ends: a WebSocket disconnect (fast path) or the
        idle sweeper (backstop that also covers SSE, which has no disconnect signal).
        Reads the rolling summary + remaining buffer and writes a single episodic
        point so the conversation is recallable later.

        Idempotent and best-effort: a missing/expired session, or a caller who does
        not own the conversation, is a no-op (ownership enforced by the read). The
        session is left in place so the user can resume seamlessly until ``ttl_s``
        evicts it; ``mark_finalized`` keeps the sweeper from re-embedding it until
        new activity clears the mark.
        """
        with telemetry.turn_span(
            "conversation_end", user_id=user_id, conversation_id=conversation_id
        ):
            try:
                snapshot = await self._working.peek(conversation_id, user_id)
            except UnauthorizedError:
                return
            if snapshot is None:
                return
            await self._episodic.write_conversation(
                user_id, conversation_id, snapshot.summary, snapshot.buffer
            )
            await self._working.mark_finalized(conversation_id)

    async def sweep_idle(self, idle_finalize_s: float) -> None:
        """Finalize every conversation idle past ``idle_finalize_s``.

        The transport-agnostic backstop for conversation-end: SSE has no disconnect
        signal and even WebSockets can drop abruptly without firing their handler.
        Each conversation is finalized at most once per idle cycle (``mark_finalized``);
        failures are isolated so one bad conversation does not stall the sweep.
        """
        for conversation_id, user_id in await self._working.due_for_finalize(
            idle_finalize_s
        ):
            try:
                await self.end_conversation(user_id, conversation_id)
            except Exception:
                # Isolate per-conversation failures so one bad conversation does not
                # stall the sweep — but log it (no longer silently suppressed).
                # M9: a finalize-failure metric hook attaches here.
                logger.warning(
                    "idle finalize failed (user_id=%s conversation_id=%s)",
                    user_id,
                    conversation_id,
                    exc_info=True,
                )

    def _enqueue(
        self, coro, *, operation: str, user_id: str, conversation_id: str
    ) -> None:
        """Fire-and-forget a background memory write, with error isolation + logging."""
        task = asyncio.create_task(
            _guard(coro, operation, user_id, conversation_id)
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def drain(self) -> None:
        """Await outstanding background writes (useful in tests / shutdown)."""
        if self._bg_tasks:
            await asyncio.gather(*list(self._bg_tasks), return_exceptions=True)


async def _guard(
    coro, operation: str, user_id: str, conversation_id: str
) -> None:
    """Run a background write, isolating + logging any terminal failure.

    The single choke point where background-write failures surface. A ``StoreWriteError``
    here means the store write exhausted its retries; an ``llm_kit.LLMError`` means the
    upstream LLM/embedder step failed (already retried by llm_kit). Either way it is
    logged once with full context and traceback. ``CancelledError`` is re-raised so
    ``drain``/shutdown cancellation still works.

    The span here nests under the turn's trace: ``asyncio.create_task`` copies the OTel
    context active at ``_enqueue`` time, so this background write shows up in the same
    conversation trace even though it runs after ``TurnComplete``.
    """
    try:
        with telemetry.span(operation, user_id=user_id, conversation_id=conversation_id):
            await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        # M9: a background-write-failure counter (by operation) attaches here.
        logger.exception(
            "background memory write failed: %s (user_id=%s conversation_id=%s)",
            operation,
            user_id,
            conversation_id,
        )


def _accumulate(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
    )
