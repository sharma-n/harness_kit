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
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from llm_kit import Message, StreamEnd, TextChunk, ToolCall
from llm_kit.llm.response import TokenUsage

from harness_kit.agent.context import ContextBuilder
from harness_kit.agent.events import (
    AgentEvent,
    TextDelta,
    ToolApprovalRequired,
    ToolCallStarted,
    ToolResult,
    TurnComplete,
    TurnFailed,
)
from harness_kit.config import AgentConfig
from harness_kit.errors import UnauthorizedError
from harness_kit.llm import LLM
from harness_kit.memory.episodic import EpisodicMemory
from harness_kit.memory.factual import FactualMemory
from harness_kit.memory.working import WorkingMemory
from harness_kit.stores.types import Turn
from harness_kit.tools.registry import ToolRegistry
from harness_kit import telemetry, metrics as _metrics

logger = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        llm: LLM,
        context_builder: ContextBuilder,
        registry: ToolRegistry,
        working: WorkingMemory,
        episodic: EpisodicMemory | None,
        factual: FactualMemory,
        cfg: AgentConfig,
        llm_factory: Callable[[str], LLM] | None = None,
    ) -> None:
        self._llm = llm
        self._llm_factory = llm_factory
        self._context = context_builder
        self._registry = registry
        self._working = working
        self._episodic = episodic
        self._factual = factual
        self._cfg = cfg
        self._bg_tasks: set[asyncio.Task] = set()
        # call_id -> (future, conversation_id) to ensure approvals are only resolved
        # by connections that own the conversation.
        self._pending_approvals: dict[str, tuple[asyncio.Future[bool], str]] = {}

    async def run_turn(
        self, user_id: str, conversation_id: str, user_message: str
    ) -> AsyncIterator[AgentEvent]:
        _t0 = time.monotonic()
        _ttft_recorded = False
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

            # Two-gate check: factory present (service manages its own LLM) AND this
            # conversation has a model override. Both required; gate 1 is a capability
            # check, gate 2 is per-conversation intent.
            llm = self._llm
            if self._llm_factory is not None:
                model_override = await self._working.get_model_name(conversation_id, user_id)
                if model_override:
                    llm = self._llm_factory(model_override)

            messages = list(ctx.messages)

            usage = TokenUsage()
            iterations = 0
            stop_reason = "completed"
            assistant_texts: list[str] = []
            tool_turns: list[Turn] = []  # Persisted tool-call/tool-result turns

            deadline = (
                asyncio.get_event_loop().time() + self._cfg.per_turn_budget_s
                if self._cfg.per_turn_budget_s
                else None
            )

            try:
                for iterations in range(1, self._cfg.max_iterations + 1):
                    if deadline is not None and asyncio.get_event_loop().time() > deadline:
                        stop_reason = "turn_budget_exceeded"
                        break

                    response = None
                    async for event in llm.invoke_stream(messages, tools=ctx.tools):
                        if isinstance(event, TextChunk):
                            if event.text:
                                if not _ttft_recorded:
                                    _metrics.record_ttft(time.monotonic() - _t0)
                                    _ttft_recorded = True
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
                    async with contextlib.aclosing(
                        self._execute_tool_calls(user_id, conversation_id, tool_calls, messages, tool_turns)
                    ) as gen:
                        async for event in gen:
                            yield event
                else:
                    # for-loop exhausted without break → hit the iteration cap.
                    stop_reason = "max_iterations"

                await self._persist(user_id, conversation_id, user_message, assistant_texts, tool_turns)
                _metrics.record_turn(time.monotonic() - _t0, iterations)
                turn.set_attributes(
                    iterations=iterations,
                    stop_reason=stop_reason,
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                )
                yield TurnComplete(usage=usage, iterations=iterations, stop_reason=stop_reason)
            except Exception as e:
                # Crash safety: persist whatever state we have before the failure.
                try:
                    await self._persist(user_id, conversation_id, user_message, assistant_texts, tool_turns)
                except Exception:
                    # If persistence itself fails, log but don't mask the original error.
                    logger.exception("failed to persist partial turn state after loop exception")
                # Emit error event so client gets a frame instead of ungraceful disconnect.
                error_msg = f"turn failed: {type(e).__name__}: {str(e)}"
                yield TurnFailed(error=error_msg)
                # Re-raise so telemetry/outer handlers still see the failure.
                raise

    async def _call_worker(
        self,
        user_id: str,
        conversation_id: str,
        call: ToolCall,
        index: int,
        queue: asyncio.Queue[AgentEvent | _WorkerDone],
    ) -> None:
        """Run one tool call end-to-end (approval gate + execution), pushing every
        ``AgentEvent`` it produces onto ``queue`` in real time, finishing with
        exactly one ``_WorkerDone`` so the fan-in loop can append this call's
        message/turns in the right slot once all calls are done.

        Mirrors the exact approval + execution logic that used to live inline in
        ``run_turn``'s ``for call in tool_calls`` loop — unchanged in substance,
        just retargeted from ``yield`` to ``await queue.put`` since this now runs
        as an independent ``asyncio.Task``, not inline in the generator.
        """
        try:
            policy = self._registry.get_policy(call.name)
            if policy and policy.requires_approval:
                fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
                self._pending_approvals[call.id] = (fut, conversation_id)
                await queue.put(
                    ToolApprovalRequired(
                        call_id=call.id,
                        name=call.name,
                        arguments=call.arguments,
                        timeout_s=policy.approval_timeout_s,
                    )
                )
                timed_out = False
                try:
                    approved = await asyncio.wait_for(fut, timeout=policy.approval_timeout_s)
                except asyncio.TimeoutError:
                    approved = False
                    timed_out = True
                finally:
                    self._pending_approvals.pop(call.id, None)
                if not approved:
                    reason = (
                        f"tool {call.name!r} — approval request timed out"
                        if timed_out
                        else f"tool {call.name!r} — user denied approval"
                    )
                    await queue.put(ToolResult(call_id=call.id, name=call.name, ok=False, content=reason))
                    await queue.put(_WorkerDone(index, Message.tool_result(call.id, reason), []))
                    return

            await queue.put(
                ToolCallStarted(call_id=call.id, name=call.name, arguments=call.arguments)
            )
            execution = await self._registry.execute(user_id, call)
            await queue.put(
                ToolResult(
                    call_id=execution.call_id,
                    name=execution.name,
                    ok=execution.ok,
                    content=execution.display,
                )
            )
            turns = [
                Turn(role="assistant", text="", tool_calls=[call]),
                Turn(role="tool", text=execution.display, tool_call_id=call.id),
            ]
            await queue.put(
                _WorkerDone(index, Message.tool_result(call.id, execution.observation), turns)
            )
        except Exception as exc:
            # Belt-and-suspenders: ToolRegistry.execute() never raises, but a bug
            # here (or in a future change) must still degrade to a failed
            # observation for THIS call, not abort the whole batch/turn — the
            # same "tool errors are observations, never exceptions" invariant
            # the sequential code already relied on. asyncio.CancelledError is a
            # BaseException, not caught here, so generator-teardown cancellation
            # (see _execute_tool_calls) still propagates correctly.
            logger.exception(
                "unexpected error in tool worker (call_id=%s name=%s)", call.id, call.name
            )
            reason = f"tool {call.name!r} failed unexpectedly: {exc}"
            await queue.put(ToolResult(call_id=call.id, name=call.name, ok=False, content=reason))
            await queue.put(_WorkerDone(index, Message.tool_result(call.id, reason), []))

    async def _execute_tool_calls(
        self,
        user_id: str,
        conversation_id: str,
        tool_calls: list[ToolCall],
        messages: list[Message],
        tool_turns: list[Turn],
    ) -> AsyncIterator[AgentEvent]:
        """Run every call in ``tool_calls`` concurrently; stream events in real
        arrival order, then append each call's ``Message``/``Turn`` entries to
        ``messages``/``tool_turns`` (mutated in place) in ORIGINAL ``tool_calls``
        order once every call has finished — the one ordering guarantee the LLM's
        message history depends on. Completion order is irrelevant.
        """
        queue: asyncio.Queue[AgentEvent | _WorkerDone] = asyncio.Queue()
        tasks = [
            asyncio.create_task(
                self._call_worker(user_id, conversation_id, call, index, queue)
            )
            for index, call in enumerate(tool_calls)
        ]
        results: list[tuple[Message, list[Turn]] | None] = [None] * len(tool_calls)
        remaining = len(tasks)
        try:
            while remaining > 0:
                item = await queue.get()
                if isinstance(item, _WorkerDone):
                    results[item.index] = (item.message, item.tool_turns)
                    remaining -= 1
                else:
                    yield item
        finally:
            # Covers both normal completion (no-op here) and early teardown —
            # e.g. the WS client disconnects mid-batch and the caller stops
            # driving this generator. Without this, abandoned worker tasks
            # (possibly still parked on an approval future) would keep running
            # in the background forever, and the entry in
            # ``self._pending_approvals`` would leak.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        for message, turns in results:  # every slot is filled: remaining hit 0
            messages.append(message)
            tool_turns.extend(turns)

    async def _persist(
        self,
        user_id: str,
        conversation_id: str,
        user_message: str,
        assistant_texts: list[str],
        tool_turns: list[Turn] | None = None,
    ) -> None:
        # Synchronous, hot-path write to working memory.
        await self._working.append_turn(conversation_id, Turn(role="user", text=user_message))
        final_text = "\n".join(assistant_texts).strip()
        if final_text:
            await self._working.append_turn(
                conversation_id, Turn(role="assistant", text=final_text)
            )
        # Append tool turns (call + result pairs) in order.
        if tool_turns:
            for tool_turn in tool_turns:
                await self._working.append_turn(conversation_id, tool_turn)

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

    def resolve_approval(self, call_id: str, approved: bool, *, conversation_id: str) -> None:
        """Resolve a pending tool-approval request from the serving layer.

        Called by the WebSocket handler when the client sends an approval message,
        or immediately by the SSE handler to auto-deny (SSE is one-way). A stale
        or unknown ``call_id``, or one belonging to a different conversation, is
        silently ignored.
        """
        entry = self._pending_approvals.pop(call_id, None)
        if entry is not None:
            fut, owner_conversation_id = entry
            if owner_conversation_id == conversation_id and not fut.done():
                fut.set_result(approved)

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
            if self._episodic is not None:
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


@dataclass(slots=True)
class _WorkerDone:
    """Internal sentinel: one tool-call worker has finished.

    Carries the ``Message``/``Turn`` entries the fan-in loop must append to the
    turn's running history — applied in the ORIGINAL ``tool_calls`` order once
    every worker has reported done, never in completion order. Keyed by
    ``index`` (not ``call.id``) so a duplicate/malformed id from a buggy LLM
    response can't silently clobber another call's result.
    """

    index: int
    message: Message
    tool_turns: list[Turn]


def _accumulate(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
    )
