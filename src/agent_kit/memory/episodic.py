"""Episodic memory (SPEC §7.2, §6.4): retrieval over the per-user vector store.

Read (retrieve) is on the hot path every turn. Write is off the hot path — the
agent loop enqueues it after ``TurnComplete``; the method exists here but is not
called synchronously during a turn.
"""

from __future__ import annotations

import time
import uuid

from pydantic import BaseModel

from agent_kit.config import EpisodicMemoryConfig
from agent_kit.llm import LLM, Embedder
from agent_kit.stores.base import VectorStore
from agent_kit.stores.types import MemoryHit, MemoryPoint, Turn


class StandaloneQuery(BaseModel):
    """Schema for the optional query-rewrite step (SPEC §6.4.3)."""

    query: str


class EpisodicMemory:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        cfg: EpisodicMemoryConfig,
        *,
        llm: LLM | None = None,
    ) -> None:
        self._store = vector_store
        self._embedder = embedder
        self._cfg = cfg
        self._llm = llm  # only needed when query_rewrite is enabled

    async def retrieve(
        self, user_id: str, user_message: str, recent_turns: list[Turn]
    ) -> list[MemoryHit]:
        """Top-k hits above ``min_score`` for this user, or ``[]`` (inject nothing)."""
        query_text = await self._build_query(user_message, recent_turns)
        vector = (await self._embedder.embed_one(query_text)).vector
        return await self._store.search(
            user_id, vector, k=self._cfg.top_k, min_score=self._cfg.min_score
        )

    async def write(
        self, user_id: str, conversation_id: str, turn: Turn
    ) -> None:
        """Embed a turn and upsert it as one episodic point (off the hot path)."""
        if not turn.text:
            return
        vector = (await self._embedder.embed_one(turn.text)).vector
        point = MemoryPoint(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "user_id": user_id,
                "conversation_id": conversation_id,
                "text": turn.text,
                "role": turn.role,
                "ts": time.time(),
            },
        )
        await self._store.add([point])

    async def _build_query(self, user_message: str, recent_turns: list[Turn]) -> str:
        # Default: the current message. Context-augment with recent turns so
        # pronouns/ellipsis in follow-ups resolve.
        augment_n = self._cfg.query_augment_turns
        if augment_n > 0 and recent_turns:
            tail = recent_turns[-augment_n:]
            prefix = " ".join(t.text for t in tail if t.text)
            base = f"{prefix} {user_message}".strip() if prefix else user_message
        else:
            base = user_message

        if self._cfg.query_rewrite and self._llm is not None:
            return await self._rewrite(base)
        return base

    async def _rewrite(self, text: str) -> str:
        from llm_kit import Message

        resp = await self._llm.invoke(
            [
                Message.system(
                    "Rewrite the user's text into a single standalone search query "
                    "that resolves pronouns and ellipsis. Return only the query."
                ),
                Message.user(text),
            ],
            response_model=StandaloneQuery,
        )
        if resp.parsed is not None:
            return resp.parsed.query  # type: ignore[attr-defined]
        return text
