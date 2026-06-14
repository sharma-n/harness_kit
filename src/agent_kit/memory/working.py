"""Working memory (SPEC §7.1): recent-turn buffer + rolling summary over Redis.

Read is hot (every turn); append is synchronous (microseconds). Rollover is
**token-budget-driven**: when the verbatim buffer's estimated size exceeds
``buffer_token_budget``, the oldest turns are folded into the rolling summary via
an ``invoke`` + ``response_model`` call and dropped from the buffer. Rollover runs
off the hot path — the agent loop enqueues ``maybe_rollover`` after ``TurnComplete``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from agent_kit.config import WorkingMemoryConfig
from agent_kit.llm import LLM
from agent_kit.stores.base import SessionStore
from agent_kit.stores.types import SessionState, Turn
from agent_kit.tokens import Estimator, estimate_tokens


@dataclass(slots=True)
class WorkingSnapshot:
    """What the context builder needs from working memory for one turn."""

    buffer: list[Turn]
    summary: str


class RolledSummary(BaseModel):
    """Schema for the rollover summarizer (oldest turns → updated summary)."""

    summary: str


class WorkingMemory:
    def __init__(
        self,
        session_store: SessionStore,
        cfg: WorkingMemoryConfig,
        *,
        llm: LLM | None = None,
        estimator: Estimator = estimate_tokens,
    ) -> None:
        self._store = session_store
        self._cfg = cfg
        self._llm = llm  # only needed for rollover summarization
        self._estimate = estimator

    async def load(self, conversation_id: str, user_id: str) -> WorkingSnapshot:
        """Read the buffer + rolling summary for a conversation.

        Creates and persists an empty, user-owned session on first contact so the
        session is owned from turn one (enforces ownership on every later load).
        """
        state = await self._store.load(conversation_id, user_id)
        if state is None:
            state = SessionState(user_id=user_id)
            await self._store.save(conversation_id, state)
        return WorkingSnapshot(
            buffer=list(state.working_buffer), summary=state.rolling_summary
        )

    async def peek(self, conversation_id: str, user_id: str) -> WorkingSnapshot | None:
        """Read-only load: return the snapshot, or ``None`` if the session is
        absent/expired. Unlike ``load`` it never creates a session — used by the
        conversation-end / idle-finalize path, which must not resurrect dead state.
        """
        state = await self._store.load(conversation_id, user_id)
        if state is None:
            return None
        return WorkingSnapshot(
            buffer=list(state.working_buffer), summary=state.rolling_summary
        )

    async def append_turn(self, conversation_id: str, turn: Turn) -> None:
        await self._store.append_turn(conversation_id, turn)

    async def due_for_finalize(self, idle_s: float) -> list[tuple[str, str]]:
        """``(conversation_id, user_id)`` for idle, not-yet-finalized conversations."""
        return await self._store.due_for_finalize(idle_s)

    async def mark_finalized(self, conversation_id: str) -> None:
        await self._store.mark_finalized(conversation_id)

    def needs_rollover(self, buffer: list[Turn]) -> bool:
        """True when the verbatim buffer exceeds its token budget."""
        return self._buffer_tokens(buffer) > self._cfg.buffer_token_budget

    async def maybe_rollover(self, conversation_id: str, user_id: str) -> None:
        """Summarize the oldest turns into the rolling summary if over budget.

        Off the hot path. No-op when there is no LLM to summarize with, nothing to
        evict, or the summarizer returns nothing usable — in those cases the buffer
        is left intact so no turns are silently lost.
        """
        if self._llm is None:
            return
        state = await self._store.load(conversation_id, user_id)
        if state is None:
            return
        kept, evicted = self._split_for_rollover(state.working_buffer)
        if not evicted:
            return
        new_summary = await self._summarize(state.rolling_summary, evicted)
        if not new_summary or new_summary == state.rolling_summary:
            return
        state.rolling_summary = new_summary
        state.working_buffer = kept
        await self._store.save(conversation_id, state)

    def _split_for_rollover(self, buffer: list[Turn]) -> tuple[list[Turn], list[Turn]]:
        """Split into (kept newest within budget, evicted oldest). Always keeps the
        newest turn even if it alone exceeds the budget."""
        used = 0
        keep_from = len(buffer)  # index where the kept (newest) slice begins
        for turn in reversed(buffer):
            cost = self._estimate(turn.text)
            if keep_from < len(buffer) and used + cost > self._cfg.buffer_token_budget:
                break
            used += cost
            keep_from -= 1
        return buffer[keep_from:], buffer[:keep_from]

    def _buffer_tokens(self, buffer: list[Turn]) -> int:
        return sum(self._estimate(t.text) for t in buffer)

    async def _summarize(self, prior_summary: str, evicted: list[Turn]) -> str:
        from llm_kit import Message

        transcript = "\n".join(f"{t.role}: {t.text}" for t in evicted if t.text)
        if not transcript:
            return prior_summary
        resp = await self._llm.invoke(  # type: ignore[union-attr]
            [
                Message.system(
                    "You maintain a running summary of a conversation. Fold the new "
                    "turns into the existing summary, preserving durable facts, "
                    "decisions, and open threads. Return only the updated summary."
                ),
                Message.user(
                    f"Existing summary:\n{prior_summary or '(none)'}\n\n"
                    f"New turns to fold in:\n{transcript}"
                ),
            ],
            response_model=RolledSummary,
        )
        if resp.parsed is not None and resp.parsed.summary:  # type: ignore[attr-defined]
            return resp.parsed.summary  # type: ignore[attr-defined]
        return prior_summary

    @property
    def buffer_turns(self) -> int:
        return self._cfg.buffer_turns
